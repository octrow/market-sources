# market-sources

<p align="center"><img src="https://raw.githubusercontent.com/octrow/market-sources/main/docs/social-preview.png" alt="market-sources: async Python scrapers for Kaspi.kz, OLX.kz and Ozon.kz; a Kaspi search for "iphone 15" finds 32 offers from 379500 KZT" width="760"></p>

<p align="center">
  <a href="https://github.com/octrow/market-sources/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/octrow/market-sources/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://pypi.org/project/market-sources/"><img alt="PyPI" src="https://img.shields.io/pypi/v/market-sources"></a>
  <img alt="Python 3.12+" src="https://img.shields.io/badge/python-3.12%2B-blue">
  <a href="LICENSE"><img alt="MIT license" src="https://img.shields.io/badge/license-MIT-green"></a>
</p>

<p align="center"><b>English</b> | <a href="README.ru.md">Русский</a></p>

Async Python scrapers for **Kaspi.kz**, **OLX.kz** and **Ozon.kz** behind one `ProductSource` protocol.
Search a product, get every seller's offer with price and delivery, read reviews and specs. Built for people
who track prices and offers on Kazakhstan marketplaces: price monitors, deal bots, purchase research.

Under the adapters sit two reusable layers: `PoliteClient`, a paced, retrying async HTTP client, and
`BrowserSession`, a persistent headless Chromium for sites that need a real browser.

```python
import asyncio
from market_sources import KaspiSource

async def main():
    kaspi = KaspiSource()
    products = await kaspi.search("iphone 15")
    offers = await kaspi.offers(products[0].product_id)
    cheapest = min(offers, key=lambda o: o.price)
    print(products[0].title, "|", len(offers), "offers, from", cheapest.price, cheapest.currency)
    await kaspi.aclose()

asyncio.run(main())
```

A real run on 2026-10-13 (Kaspi search took 1.4 s and returned 12 products):

```text
Смартфон Apple iPhone 15 128Gb NanoSIM+eSIM черный | 32 offers, from 379500.0 KZT
```

Each offer is a plain Pydantic model:

```python
RawOffer(source='kaspi', product_id='113137790', seller='Kaz_Phones', price=379500.0, currency='KZT',
         delivery='EXPRESS', in_stock=True, merchant_rating=4.8, merchant_review_count=32,
         delivery_date='2026-10-13', delivery_cost=995.0, ...)
```

## Install

```bash
pip install market-sources                  # Kaspi; OLX needs the impersonate extra (below)
pip install "market-sources[impersonate]"   # + curl_cffi, so OLX.kz's WAF lets requests through
pip install "market-sources[browser]"       # + playwright, for Ozon.kz
playwright install chromium                 # once, for the browser extra
```

With uv: `uv add "market-sources[impersonate,browser]"`. Latest code from GitHub:
`pip install "git+https://github.com/octrow/market-sources"`.

Python 3.12+. Runtime dependencies are `httpx` and `pydantic`. Optional extras are imported lazily, so
`import market_sources` works without them.

## Sources

| source | how it gets data | needs | notes |
|---|---|---|---|
| `KaspiSource` | Kaspi's internal JSON API over `PoliteClient` | base install | `search`, `offers`, `reviews`, `reviews_with_stats`, `specs`; `KASPI_PROXY` env var for a proxy |
| `OlxSource` | OLX's public JSON API | `impersonate` extra | CloudFront rejects httpx's TLS fingerprint; with `curl_cffi` installed the client impersonates Chrome automatically (a search took 1.9 s and returned 63 listings in the run above) |
| `OzonSource` | Ozon's composer API, read through a real browser | `browser` extra | session saved to `OZON_STATE_PATH` (default `~/.config/market_sources/ozon_state.json`); `OZON_BASE_URL` overrides the host |

Every source implements the same protocol, so consumer code doesn't care which site it talks to:

```python
class ProductSource(Protocol):
    async def search(self, query: str, city: str | None = None) -> list[RawProduct]: ...
    async def offers(self, product_id: str) -> list[RawOffer]: ...
    async def reviews(self, product_id: str) -> list[RawReview]: ...
    async def specs(self, product_id: str) -> dict[str, str]: ...
```

## Modules

| module | what it is |
|---|---|
| `market_sources.base` | `ProductSource` Protocol + `RawProduct` / `RawOffer` / `RawReview` / `RawReviewStats` |
| `market_sources.polite_http` | `PoliteClient`: semaphore + jittered delay + browser headers + proxy + retry/backoff |
| `market_sources.impersonate` | `ImpersonateTransport`: an httpx transport that sends through `curl_cffi` with a Chrome TLS fingerprint |
| `market_sources.playwright_session` | `BrowserSession`: lazy persistent Chromium, serialized navigation, ready hook, relaunch-retry |
| `market_sources.kaspi` | `KaspiSource` / `KaspiClient` / pure `parse_*`: internal JSON API |
| `market_sources.olx` | `OlxSource` / `OlxClient` / pure `parse_*`: public JSON API |
| `market_sources.ozon` | `OzonSource` / `OzonBrowser` / pure `parse_*`: composer API via browser |

## How it differs from a plain httpx script or Scrapy

- **A bare httpx request gets a 403.** Each site has its own gate (Kaspi checks the referer, OLX fingerprints
  TLS, Ozon serves a JavaScript challenge). The adapters already pass them, and the reasons are written down
  below, so you don't rediscover them by trial.
- **Scrapy is a crawler framework** with its own runtime, pipelines and settings. This is a small async library
  you call from your own code (a bot, a FastAPI app, a cron script) and get Pydantic models back.
- **Pacing is built in and hard to forget.** Concurrency is capped inside the client, delays are jittered, and
  `Retry-After` is honored, so `asyncio.gather` over 40 pages won't hammer a site.
- **Raw data, not opinions.** The models keep what the site said. Normalization, currency conversion, dedup and
  ranking are left to you.

Please use it responsibly: keep the default pacing, cache what you can, and respect each marketplace's terms.

## Design rules

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

## Origin

Extracted from the author's `buy-smarter` project (`src/buysmarter/sources/`). The polite-HTTP and
browser-session design is informed by three more independent implementations of the same pattern in
`olx-almaty-phone-analyzer` and `get_cool_event`.

### Changes from the original code

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
uv run pytest
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Report vulnerabilities privately: [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE)
