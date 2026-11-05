# Logic map

How a caller's query or product id becomes `RawProduct`, `RawOffer`, `RawReview`, a specs dict or a `KimovilPhone`.
The map describes the code as it is today, step by step.
Each step says what it decides, where the flow branches, and which module does it.

There is no glossary file; terms: "source" = a marketplace adapter, "client" = the thin endpoint wrapper under it, "parse" = the pure JSON/HTML-to-model functions.
Known gaps and fixes in progress are in `docs/plans/2026-11-05-logic-map-fixes.md`.

Rule: if you change a step or a branch, update this file in the same commit.
`tests/test_logic_map.py` checks that every `Module:` line points to a real module.
This file is the only copy; there is no mirror.

## The flow at a glance

```
caller: <Source>(...).search / offers / reviews / specs        (lookup for Kimovil)
  │
  ├─ 1  Build the source ........ which client, proxy, transport?
  ├─ 2  Resolve the city ........ name -> site id, or ValueError
  ├─ 3  Build the request ....... per-site params, budget, paging
  ├─ 4  Send it ................. PoliteClient (httpx / curl_cffi) or BrowserSession
  ├─ 5  Retry and classify ...... retry what, wait how long, raise what
  ├─ 6  Parse ................... JSON/HTML dict -> Raw* models, drop what is unusable
  ├─ 7  Remember ................ per-source caches that later calls read
  ├─ 8  offers / 9 reviews / 10 specs   per-site shape of each call
  ├─ 11 Kimovil lookup .......... marketplace title -> phone specs page
  └─ 12 Close ................... pools and browsers
  │
Raw* models (no normalisation: the consumer does that)
```

The library has no CLI, GUI, API or worker: the entry points are the public methods of the sources.
Normalising to canonical models, caching results and choosing sources are the consumer's job.

## Where the flow splits by site

Each site decides its own gate, id shape, city handling and anti-bot path. There is no registry; each source module owns its split.

| Site | Transport | Anti-bot gate | City | Id the per-product calls want |
|---|---|---|---|---|
| Kaspi | `PoliteClient` over httpx | `referer` + `x-ks-city` header + city cookie | `cityId` header and `c=` param | `configSku` if the card has one, else numeric id |
| OLX | `PoliteClient` + `ImpersonateTransport` when curl_cffi is installed, else plain httpx | TLS fingerprint | `city_id`, or `region_id` with `region=True` | listing id |
| Ozon | `BrowserSession` (headless Chromium, navigation) | JS challenge on the home page | none (single region) | sku or `/product/...` path |
| Kimovil | `PoliteClient` + `ImpersonateTransport` when installed | Cloudflare | none | slug from autocomplete |

Examples:
- `KaspiSource.search("iphone 15", city="Астана")` → cityId 710000000 in header, cookie and `c=`.
- `OlxSource.search("iphone", region=True)` → `region_id=8` (Almaty oblast) instead of `city_id=1`.
- `OzonSource.search("iphone", city="Астана")` → `city` is ignored.

Module: `cities`, `kaspi/client`, `olx/client`, `ozon/browser`, `kimovil/client`

## Entry points

All sources take an optional injected client (or browser) and build their own when it is missing. They differ in defaults.

| Entry | City default | Proxy | Notes |
|---|---|---|---|
| `KaspiSource(client=None, proxy=None)` | Almaty `750000000` | `proxy` arg, else `$KASPI_PROXY` (read in `KaspiClient`) | extra cities get their own `KaspiClient` |
| `OlxSource(client=None, proxy=None)` | Almaty city_id `1` | `proxy` arg only; no env var | extra cities and regions get their own `OlxClient` |
| `OzonSource(browser=None, base_url=None)` | none | none | host `base_url`, else `$OZON_BASE_URL`, else `https://www.ozon.kz`; saved session from `$OZON_STATE_PATH` or `~/.config/market_sources/ozon_state.json`, used only if the file exists at construction |
| `KimovilSource(client=None, proxy=None)` | none | `proxy` arg only | not a `ProductSource` |
| `PoliteClient(...)` | none | `proxy` arg | defaults: concurrency 2, delay 0.5-1.5 s, timeout 10 s, 1 retry |
| `BrowserSession(...)` | none | none | defaults: headless, idle close after 600 s, nav timeout 90 s |

`ProductSource` (`base`) is a runtime-checkable Protocol: `isinstance` checks only that `name`, `search`, `offers`, `reviews`, `specs` exist, not their signatures.

Module: `base`, `kaspi/source`, `olx/source`, `ozon/source`, `kimovil/source`, `polite_http`, `playwright_session`

## Steps

### 1. Build the source

Decides: which client or browser the source uses, and with which proxy and transport.
In: optional injected client, `proxy`. Out: a source holding one default client.
Branches:
- client injected → used as is, `proxy` ignored;
- Kaspi, no client → `KaspiClient`, proxy = arg, else `$KASPI_PROXY` (empty string counts as unset);
- OLX / Kimovil, no client → client with `ImpersonateTransport` if `curl_cffi` is importable, else plain httpx;
- Ozon, no browser → `OzonBrowser`; state file attached only if it exists now.
No curl_cffi: OLX and Kimovil still construct and send through httpx (a warning is logged at construction); the WAF then answers 403 (see step 5).
Fails: nothing is opened yet; clients are lazy (httpx client on first request, Chromium on first navigation).
Module: `kaspi/source`, `olx/source`, `ozon/source`, `kimovil/source`, `impersonate`

### 2. Resolve the city

Decides: which site city id a call uses.
In: `city` as a name (Russian or Latin, any case), a digit string, or None. Out: site id or None.
Branches:
- None or "" → default client (no extra client);
- all digits → passed through as the site's own id, unchecked;
- known name or alias ("нур-султан" → Астана) → id from the 21-city table;
- unknown name → `ValueError("unknown city ...")`.
Same id as the default client's → the default client is reused; any other id → a client cached per city, closed by `aclose`.
Extra clients copy `client.http.proxy` from the default client. When that is None (no proxy, or an injected client without `http`), they use the source's own `proxy` argument; if that is None too, Kaspi extra clients fall back to `$KASPI_PROXY` and OLX extra clients get no proxy.
Ozon: `city` is accepted and ignored.
Fails: fail-closed with `ValueError` before any request.
Module: `cities`, `kaspi/source`, `olx/source`

### 3. Build the request

Decides: the exact params for the site's search endpoint.
Branches (Kaspi `search`):
- `budget` below 500 000 → `q=:price:<bucket>` for every bucket starting at or below the budget, so the ceiling is the top of the budget's bucket ("до 15 000" returns up to 49 999);
- `budget` at or above 500 000 → no price filter;
- `filters` (facet row ids) are appended to `q` after the price;
- `page` passed to the client only when non-zero; the client always sends `page` (0 by default); Kaspi answers pages 0 and 1 only.
Branches (OLX `search`):
- `budget` → `filter_float_price:to`, `min_price` → `:from`, both inclusive;
- `region=True` and the city has a known region → `region_id` replaces `city_id`; unknown region → plain city search;
- first page has fewer than 50 parsed listings and `len(data) < total_elements` → one more request at `offset=len(data)`;
- `search_cheapest`: sorted price ascending, `min_price=1` (free ads skipped), pages step by the number of listings the previous page returned (0, then 40, 80 ... if OLX returns 40), up to `limit` (default 200) and 1000; stops when a page has fewer than 25 items; optional `category_id`;
- `search_all(min, max)`: first page of the range; `total_elements` below 1000 or range width at most 1000 KZT → one slice, else split at the midpoint and recurse; then every remaining page of every slice is fetched with `asyncio.gather`, stepping by the size of the slice's first page (50 when that page has under 25 listings); city only, no `region`.
Branches (Ozon `search`): path `/search/?text=...&from_global=true`, plus `currency_price=0.000;<budget>.000` when `budget` is given; wrapped into a composer-api URL.
Kimovil: no search request here; see step 11.
Fails: a refused or 4xx request is step 5; `search_all` slices that stay capped (width at most 1000 KZT, still 1000 results) are returned truncated, with a warning naming the slice.
`search_all` gathers all pages in one `asyncio.gather` with `return_exceptions`: a failed page is logged (warning, with its offset) and skipped, the rest are kept; a failed second page of `search` still loses the first.
Module: `kaspi/client`, `kaspi/source`, `olx/client`, `olx/source`, `ozon/source`, `ozon/browser`

### 4. Send the request

Decides: how the bytes leave the process.
HTTP sources (`PoliteClient.request`):
- one semaphore slot (default 2) held for the whole logical request, including backoff sleeps;
- one jittered pause (0.5-1.5 s; Kimovil 1.0-2.5 s) before the first attempt only;
- timeout 10 s (OLX 15 s, Kimovil 20 s); with `ImpersonateTransport` the proxy goes to curl_cffi, httpx's own proxy is not set;
- headers: Chrome UA, `accept`, `accept-language: ru-RU`, per-call headers layered over them; Kaspi adds `x-requested-with`, `referer` (search page or product page), `x-ks-city`, city cookie; OLX adds none;
- `ImpersonateTransport` drops `accept-encoding`/`host`/`connection`/`content-length`, sends via curl_cffi with `allow_redirects=False` (httpx follows redirects itself) and rebuilds an httpx response without `content-encoding`.
Browser source (`BrowserSession.page_op`):
- one asyncio lock: every navigation on the session is serialised, so Ozon calls never overlap;
- first call (or after teardown) launches Chromium, then `prepare_page`: Ozon opens the home page, waits 15 s for the challenge, fails if the title matches the anti-bot regex;
- `page.goto(composer-api URL)`; on 200 the body text is read and parsed as JSON.
- idle timer: no navigation for 600 s → browser closed under the lock, relaunched on the next call.
No curl_cffi / no playwright: see step 1; Chromium launch raises an ImportError from playwright on first Ozon call.
Fails: transport errors are step 5.
Module: `polite_http`, `impersonate`, `playwright_session`, `ozon/browser`

### 5. Retry and classify failures

Decides: whether a failed request is repeated and what the caller sees.
HTTP (`PoliteClient`):
- idempotent methods (GET, HEAD, OPTIONS, PUT, DELETE, TRACE) retry; POST retries only with `retry=True` (Kaspi offers passes it);
- retried on `httpx.TransportError` and statuses 429, 500, 502, 503, 504; default `max_retries=1`;
- wait: `Retry-After` (seconds or HTTP-date) clamped to 30 s, else `1 s * 2**attempt` plus up to 25% jitter;
- other 4xx are not retried; the last response goes through `raise_for_status`, so the caller gets `httpx.HTTPStatusError`.
Browser (`BrowserSession`):
- status 200 → success; 403 or 307 with a retry left → full teardown and relaunch (default `retries=1`);
- dead-browser error text ("closed", "crashed", "disconnect") with a retry left → same relaunch;
- final non-200: with a state file and status 401/403/307 → `OzonSessionExpired`; else 401/403/407/429/5xx → `OzonBlocked`; else `OzonError`;
- no response object (status -1) → `OzonError`;
- composer-api body that is not JSON → `OzonError`; challenge not passed → `OzonError`, page closed so the next call retries from scratch;
- a navigation over 90 s (playwright's `TimeoutError`) → `OzonError("navigation timed out")`, not retried.
Fails: all errors propagate to the caller; no source catches them except Kimovil (step 11).
Module: `polite_http`, `playwright_session`, `ozon/browser`

### 6. Parse

Decides: which raw records are kept and which fields stay None.
All parse modules are pure functions of an already decoded payload.
Kaspi `search`:
- id: leading digits of `id`, else digits from the `shopLink` slug; neither, or no title → card dropped (debug log);
- the letter suffix is dropped, so "16535441a" and "16535441b" would both become `product_id` "16535441" (#10, not confirmed);
- `review_id` = `configSku` when it differs from the numeric id; `specs_available=False` only when the id had a letter suffix and there is no `configSku`;
- price = `unitSalePrice`, else `unitPrice`; `brand` kept as sent ("Без бренда" included); `official_partner` only if a real bool;
- link: `/p/...` becomes `kaspi.kz/shop/p/...`.
OLX `search`: card needs `id` and `title`; price from the `params` entry `price` (None for "договорная"); photo placeholders `{width}`/`{height}` filled with the photo's own size or 800x600.
Ozon `search`: only the `tileGridDesktop` widget; a tile without price (installment-looking texts with "мес", "×", "/" are skipped), sku or name is dropped; widgets are found by name before the first "-" in the key; unparseable widget JSON → empty result, debug log.
Ozon product links are built on `https://www.ozon.kz` whatever `base_url` is.
Fails: items missing a key are skipped silently; a missing top-level key yields an empty list.
An Ozon star-rating or seller-rating text that is not a number becomes None instead of raising.
Module: `kaspi/parse`, `olx/parse`, `ozon/parse`, `base`

### 7. Remember

Decides: what a search leaves behind for later calls.
- Kaspi: `product_id → configSku`, `product_id → cityId`, `(query, city) → facets`; all in-memory, never evicted; the last search wins on a repeated key;
- OLX: the full raw listing of every search result by id (`_offer_cache`, keyed by `str(id)`), never evicted: use one source per run; `category_of(id)` reads it;
- Ozon: the last product page payload (path, time, payload) for 60 s, so `offers` and `specs` of one product share one navigation; Kimovil: nothing.
Side effects outside memory: none for HTTP sources. Ozon writes the browser state file only in `bootstrap` (step 12); search never writes.
Module: `kaspi/source`, `olx/source`

### 8. Offers

Decides: the list of seller offers for a product.
Kaspi: `POST /yml/offer-view/offers/{id}` (retried) in the `city=` argument, else the city `search` found the product in, else the default city; first 32 offers only (`limit=32`, page 0); offers without `price` are dropped; the offer's delivery option is the one named by `deliveryType`, else EXPRESS, TO_DOOR, else the first; delivery date converted to Asia/Almaty. `RawOffer.city` stays None.
OLX: exactly one offer built from the cached listing; cache miss → `GET /api/v1/offers/{id}/`; no price → `[]`.
Ozon: one offer from the `webPrice` and `webCurrentSeller` widgets of the product page; price = `cardPrice` (the Ozon Card price) when present, else `price`; no price → `[]`; `in_stock` is `isAvailable`, default True.
Fails: HTTP and browser errors propagate (step 5).
Module: `kaspi/source`, `kaspi/parse`, `olx/source`, `olx/parse`, `ozon/source`, `ozon/parse`

### 9. Reviews

Decides: reviews and, for Kaspi, aggregate stats.
Kaspi: `GET .../reviews/product/{api_id}` with `limit=9`, `filter=COMMENT`; `negative=True` → `limit=30`, `filter=NEGATIVE`; `review_id` arg beats the remembered `configSku`; `reviews_with_stats` reads reviews and stats from the same response; stats are None without `summary` and `groupSummary`. Uses the default city client, not the product's city. Fields `useful`, `purchased`, `has_photos` are not filled (stay None).
OLX: always `[]`, no request.
Ozon: product path + `reviews/` navigation; list from `webListReviews` (`reviews` or `items`); unknown flags stay None.
Fails: errors propagate.
Module: `kaspi/source`, `kaspi/parse`, `olx/source`, `ozon/source`, `ozon/parse`

### 10. Specs

Decides: the flat `{name: value}` dict for a product.
Kaspi: `GET /yml/content/item/product/{api_id}` with the default client's city; hidden features and empty values skipped; groups flattened, a repeated name keeps the last value.
OLX: from the cached listing: `condition` (state label), `seller_type`, `city`, `description` (HTML stripped); no network on a cache hit.
OLX `seller_type` is set only when the listing says so (`user.seller_type`, or "business" from the `business` flag); otherwise the key is absent, so an empty detail payload gives `{}`.
Ozon: the product page again (shared with `offers` for 60 s, see step 7), widget `webShortCharacteristics`.
Fails: errors propagate. Kaspi fashion cards with `specs_available=False` are not blocked here: the call is made and the caller is expected to skip it.
Module: `kaspi/source`, `kaspi/parse`, `olx/source`, `olx/parse`, `ozon/source`, `ozon/parse`

### 11. Kimovil lookup

Decides: which Kimovil phone page a marketplace title maps to.
In: a title. Out: `KimovilPhone` or None.
Steps: `model_query` drops memory sizes, bracketed text and every Cyrillic word, keeps Latin brand and model tokens; then up to 5 autocomplete calls in order: full query, query without its first word, then the query trimmed from the end down to 3 words; the first call with a confident `best_match` wins.
`best_match`: a hit qualifies only if every word of its name (minus "5g", "4g", "lte") is in the query, the first word (parent brand) excepted; at least 2 matched words; most matched words wins, then the name with fewest words, then Kimovil's order. Rumours and non-smartphone results are dropped.
Then the specs page is fetched and parsed into label → value rows (first row wins on a repeated label; a "Capacity" in mAh becomes "Battery capacity"; DxOMark score added).
No selectolax: `parse_phone` raises ImportError (not caught).
Fails: no confident match → None (info log); `httpx.HTTPError` (including 403 from Cloudflare) → None (warning log); a non-JSON 200 from autocomplete → None (warning log); a JSON body that is not an object has no hits; a title with no Latin word sends no request and returns None.
Module: `kimovil/source`, `kimovil/client`, `kimovil/parse`

### 12. Close and bootstrap

Decides: what is released and how an Ozon session is saved.
- `aclose` on a source closes the default client and every per-city or per-region client it made, then forgets them; the Raw* caches are not cleared;
- `BrowserSession.aclose` tears down page, context, browser and driver under the lock; the next call relaunches;
- `BrowserSession.bootstrap(probe_url)` (manual): opens a headed browser, polls `probe_url` every 1.5 s until status 200, for 120 s of sleep (request time not counted), then writes the storage state (file 0600, dir 0700) and `<state>.ua.json`; returns False on timeout and writes nothing; raises `ValueError` when the session has no `state_path`. `OzonBrowser.bootstrap` fills in `$OZON_STATE_PATH` (or the default file) when there is none, and the composer-api home URL as the default probe.
- On the next launch, a state file that exists restores cookies and the saved user agent.
Module: `playwright_session`, `ozon/browser`, `kaspi/source`, `olx/source`

### Output

`list[RawProduct]`, `list[RawOffer]`, `list[RawReview]`, `dict[str, str]`, `(list[RawReview], RawReviewStats | None)`, `KimovilPhone | None`.
Unknown values stay None; a missing field never reads as "no" or zero. `currency` defaults to "KZT" on every source.
`RawProduct.specs_available`, `review_id`, `brand`, `official_partner`, `category_path` are filled by Kaspi only.
Module: `base`

## Side flows

### Demo script

`examples/kaspi_demo.py` runs one Kaspi search, then the offers of the first hit; it is not imported by the package.
Feeds: steps 3, 8.
Module: `kaspi/source`

## Known gaps

Short list; details and fixes are in the plan (number in brackets).
- Kaspi variant cards that differ only by the id's letter suffix would share one `product_id`; offers, reviews and specs of one are served for both (#10, not confirmed).
- Kaspi reviews are capped at 9 (30 for negative) and offers at 32, with no paging (#5; decided to keep: the caps are intended and documented here).
