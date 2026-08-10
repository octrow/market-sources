# market-sources

Marketplace product sources behind one protocol — **Kaspi.kz**, **OLX.kz**,
**Ozon.kz** — plus the two transport layers that kept getting rewritten
underneath them: a polite retrying async HTTP client and a persistent headless
browser session.

Extracted from `buy-smarter` (`src/buysmarter/sources/`), with the
polite-HTTP/browser-session design informed by three more independent
implementations of the same pattern: `olx-almaty-phone-analyzer`
(`core/async_scraper.py`, `enrichment/kimovil_scraper_v2.py`) and
`get_cool_event` (`adapters/sites.py`).

Python ≥3.12. Runtime deps: `httpx`, `pydantic`. `playwright` is the optional
`browser` extra — needed only to *run* the Ozon adapter, never to import it.

| module | what it is |
|---|---|
| `market_sources.base` | `ProductSource` Protocol + `RawProduct` / `RawOffer` / `RawReview` |
| `market_sources.polite_http` | `PoliteClient` — semaphore + jittered delay + browser headers + proxy + retry/backoff |
| `market_sources.playwright_session` | `BrowserSession` — lazy persistent Chromium, serialized nav, ready hook, relaunch-retry |
| `market_sources.kaspi` | `KaspiSource` / `KaspiClient` / pure `parse_*` — internal JSON API |
| `market_sources.olx` | `OlxSource` / `OlxClient` / pure `parse_*` — public JSON API |
| `market_sources.ozon` | `OzonSource` / `OzonBrowser` / pure `parse_*` — composer-api via browser |

```python
from market_sources import KaspiSource, OlxSource, OzonSource, PoliteClient

source = KaspiSource()
products = await source.search("iphone 15")
offers = await source.offers(products[0].product_id)
await source.aclose()
```

## Install

```bash
uv add --editable ~/dev/pylibs/market-sources            # httpx + pydantic only
uv add --editable "~/dev/pylibs/market-sources[browser]" # + playwright, for Ozon
playwright install chromium                              # once, for the browser extra
```

## Invariants (the reason this library exists)

**Every adapter is three files, and only one of them does I/O.** `client.py` (or
`browser.py`) knows endpoints and nothing about shapes; `parse.py` is pure
functions over already-decoded `dict`s and never imports the client; `source.py`
wires them into a `ProductSource`. That split is what makes the parsers testable
against saved payloads with no network and no mocking.

**Raw in, raw out.** These models are deliberately source-shaped, not canonical.
Normalization, currency conversion, dedup and ranking belong to the consumer —
the library's job ends at "the site said this".

### polite_http

* **A bare `python-httpx` User-Agent gets 403'd by every site in scope.** The
  browser UA is not politeness theatre, it is the price of a 200. Kaspi
  additionally gates on `referer` — a page-matching referer is its *entire*
  anti-bot check, which is why `request()` takes `referer` as a first-class
  argument rather than making callers hand-roll the header.
* **Pacing is per logical request, not per attempt.** One jittered
  `min_delay_s..max_delay_s` sleep inside the semaphore, before the first
  attempt; retry backoff is additional sleep on top. `max_delay_s=0` disables
  pacing entirely (that is how the tests stay fast).
* **The semaphore is the flood guard, and it is small (2).** `asyncio.gather`
  over 40 pages is one line of consumer code away at all times; the concurrency
  cap lives in the client so no consumer can forget it.
* **Retries never replay a side effect.** `POST`/`PATCH` are excluded by
  default; a caller who knows their POST is a read (Kaspi's offers endpoint)
  passes `retry=True` explicitly. This is opt-in, one call at a time.
* **A 404 is an answer, not a hiccup.** Only `429` and `5xx` (plus transport
  errors) retry. Everything else raises through `raise_for_status()` on the
  first attempt.
* **`Retry-After` wins over the backoff formula** (seconds *or* HTTP-date),
  clamped to `backoff_max_s` so a hostile `Retry-After: 86400` cannot park the
  process for a day.
* **Backoff is exponential with jitter, capped**: `backoff_base_s * 2**attempt`
  plus up to `backoff_jitter` of itself, `min`'d with `backoff_max_s`.
* **`sleep` and `rng` are injectable.** Every pacing and backoff decision is
  therefore assertable without a clock — which is the only reason the retry
  schedule has tests at all.
* **One `AsyncClient`, reused.** The four originals built a fresh
  `httpx.AsyncClient` per request and threw away the connection pool every
  time. Call `aclose()` (or use the async context manager).

### playwright_session

* **Importing must never require playwright.** `playwright` is imported inside
  the launch path, so `import market_sources` works on a machine with only the
  base deps — a consumer that only wants Kaspi and OLX installs nothing extra.
  There is a test that blocks the import to keep this true.
* **One page, therefore one lock.** Every navigation mutates shared state
  (cookies, history, the DOM the JSON is read off), so `fetch_text` serializes
  behind an `asyncio.Lock`. Concurrency here is a correctness bug, not a
  speedup.
* **Do NOT route-block images/css/fonts.** The anti-bot challenge loads its
  assets through them; blocking earns a 403. This is the single most expensive
  lesson encoded in this module, and the reason `LAUNCH_ARGS` has no blocking.
* **The challenge is passed once per page, then cached.** `prepare_page` is the
  hook (goto home, dwell, verify the title is not the anti-bot wall); its
  success is remembered until the session is torn down.
* **Stale session → full relaunch, not a retry in place.** A `403`/`307` or a
  "target closed" error means the cookie jar is dead; only a fresh
  browser+context+page fixes it, and only once per call.
* **Idle teardown takes the lock and detaches first.** A headless Chromium
  costs ~300 MB, so it closes after `idle_timeout_s` and relaunches lazily.
  Two bugs from the original are fixed here: the idle task used to `cancel()`
  *itself* and then `await` the teardown (so the `CancelledError` landed
  mid-close and left the browser process alive), and it tore the session down
  without the nav lock (so it could yank the page out from under an in-flight
  navigation).

## Deviations from the buy-smarter originals

| change | reason |
|---|---|
| `PoliteClient` replaces per-request `httpx.AsyncClient()` | reuses the connection pool instead of discarding it per call; same headers/pacing |
| retry/backoff is exponential + jittered, honors `Retry-After` | originals used a flat `1.0 * (attempt+1)` and ignored `Retry-After` — the audit's named gap |
| Kaspi's `offers` POST passes `retry=True` | preserves the original's retry-any-method behavior under the new "idempotent only" default |
| `KASPI_PROXY` / `OZON_BASE_URL` read from the environment | dropped the dependency on buy-smarter's `config.get_settings()`; still overridable per instance |
| `BrowserSession` + `prepare_page` hook | `OzonBrowser` is now site knowledge only (challenge + composer URL); the session machinery is shared with any future playwright source |
| dead-browser detection is message-based | it no longer requires a `playwright.Error` instance, so the state machine is testable with a fake page |
| `BrowserSessionError` message is `HTTP {status} for {url}` | was `Ozon returned HTTP {status} for {path}`; the class (`OzonError`) still carries the site identity |
| every adapter grew `aclose()` | there is now a pool/browser to close; the originals had nothing to release except Ozon's browser |

## Tests

`pytest` — no network, no browser, no clock. HTTP is driven through an
`httpx.AsyncBaseTransport` double (not `respx` — one fewer dependency), the
browser through an injected `page_factory`, and time through injected `sleep` /
`rng`. Parser fixtures are minimal dicts built from the branches `parse.py`
actually reads.

```bash
cd ~/dev/pylibs/market-sources && uv run pytest
```
