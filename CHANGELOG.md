# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `OzonSource.search` takes a `budget` and caps the price server-side (`currency_price`), like OLX.
- `BrowserSession(channel=...)`: the Playwright browser channel for both the search session and
  `bootstrap`; `channel="chrome"` drives the installed Google Chrome instead of the bundled Chromium.

## [0.2.2] - 2026-10-15

### Changed

- README images get phone-width variants, so text stays readable on a 390px screen.

## [0.2.1] - 2026-10-15

### Changed

- README redesign: light/dark SVG hero and diagram, badges; images use absolute URLs so they render on PyPI.

## [0.2.0] - 2026-10-14

### Added

- `OlxSource.search_all(query, min_price, max_price)`: every listing in a price range. OLX caps a search at 1000
  results, so the range is halved until each slice fits and every page of every slice is fetched (a live
  100k–160k ₸ run for "телефон" returned 2180 listings in 40 s).
- `parse_specs` (and so `OlxSource.specs`) returns the ad text as plain text under `"description"`, so a
  consumer can read the seller's own words (defects, repairs) without a detail call.
- `OlxClient.search(min_price=...)`: server-side lower price bound (`filter_float_price:from`).
- `KimovilSource.lookup(title)` (new `kimovil` extra: curl_cffi + selectolax): a marketplace title → the phone's
  Kimovil specs and index. Resolves the model through Kimovil's autocomplete JSON, picks the hit whose name names
  nothing the title does not ("iPhone 13" never becomes the Pro Max), retries without the brand word and with
  trailing words trimmed.

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
