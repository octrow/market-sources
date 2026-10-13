# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [0.1.1] - 2026-10-13

### Changed

- The default Ozon session file moved from `~/.config/buysmarter/ozon_state.json` to
  `~/.config/market_sources/ozon_state.json`. Move the old file (and its `.ua.json` sibling) or set `OZON_STATE_PATH`
  to keep a saved session.
- The session-expired error now points at `BrowserSession.bootstrap()` instead of a command from another project.

### Docs

- README banner image, with an absolute URL so it renders on PyPI as well.

## [0.1.0] - 2026-10-13

First public release.

### Added

- `ProductSource` protocol with source-shaped Pydantic models: `RawProduct`, `RawOffer`, `RawReview`, `RawReviewStats`.
- `KaspiSource`: search, seller offers (price, delivery type, delivery date and cost, merchant rating), reviews with
  rating stats, specs. Optional proxy via `KASPI_PROXY`.
- `OlxSource`: search, the listing as a single offer, and specs (condition, seller type, city) over OLX.kz's public
  JSON API. OLX has no product reviews, so `reviews()` returns an empty list.
- `OzonSource`: search, offers, reviews and specs through Ozon.kz's composer API in a real browser, with a
  persisted session (`OZON_STATE_PATH`).
- `PoliteClient`: one pooled `httpx.AsyncClient` with a small semaphore, jittered pacing, browser headers, proxy,
  exponential backoff that honors `Retry-After`, and retries only for idempotent methods by default.
- `BrowserSession`: lazy persistent headless Chromium with serialized navigation, a `prepare_page` hook,
  relaunch on a dead session and idle teardown.
- `ImpersonateTransport`: an httpx transport over `curl_cffi` with a Chrome TLS fingerprint; `OlxClient` uses it
  automatically when the `impersonate` extra is installed, because OLX.kz's CloudFront WAF rejects httpx.
- Optional extras: `browser` (playwright), `impersonate` (curl_cffi).
