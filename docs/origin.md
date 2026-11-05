# Origin

Extracted from the author's `buy-smarter` project (`src/buysmarter/sources/`). The polite-HTTP and
browser-session design also draws on three other implementations of the same pattern in the author's
earlier scrapers.

## Changes from the original code

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
