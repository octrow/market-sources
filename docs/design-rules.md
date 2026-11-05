# Design rules and tests

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

## Tests

`pytest` — no network, no browser, no clock. HTTP is driven through an
`httpx.AsyncBaseTransport` double (not `respx` — one fewer dependency), the
browser through an injected `page_factory`, and time through injected `sleep` /
`rng`. Parser fixtures are minimal dicts built from the branches `parse.py`
actually reads.

A source takes any client of the right shape: `KaspiSource(client=...)`,
`OlxSource(client=...)` and `OzonSource(browser=...)` are typed against the
`KaspiClientLike`, `OlxClientLike` and `OzonBrowserLike` Protocols, and
`PoliteClient(rng=...)` against `polite_http.Rng`. A stub doesn't have to
subclass the real client to type-check.

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check . && uv run ty check   # what CI runs too
```
