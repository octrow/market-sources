# Plan: logic map findings

Findings from writing `docs/logic-map.md` on 2026-11-05 (unattended trace; the independent review runs separately).
The map describes the code as it is; this file says what is wrong with it and what we propose. New findings are appended.
A fixed finding gets ✅ and the map is updated in the same commit.

Severity: HIGH: wrong result for the user. MEDIUM: worse result than it could be. LOW: noise, debt, docs.
"Question": not sure if bug or intent; the owner decides. "Not confirmed": needs a live run.

## Findings

| # | Map step | What is wrong | We propose | Severity |
|---|---|---|---|---|
| 1 ✅ | 3 | `olx/source.py` `search_cheapest` and `search_all` page at offsets `range(0, ..., DEFAULT_LIMIT=50)`, but `olx/client.py:34` says OLX returns about 40 per page. If true, items 41-50 of every page are never fetched; `search` itself uses `offset=len(data)` and is safe. Not confirmed. | Live run: request `limit=50`, count `len(data)`. If under 50, step by `len(data)` like `search`. | MEDIUM (Not confirmed; HIGH for `search_all` if confirmed: it promises every listing) ✅ fixed by stepping with the page size OLX returned (no live run needed; harmless if the page is 50) |
| 2 ✅ | 12 | `ozon/browser.py:73-74` sets `state_path=None` when the default file is missing, and `playwright_session.py:303-304` then raises `ValueError` in `bootstrap`. So the first save (or a save after deleting the file, as the `SessionExpired` text at `playwright_session.py:116` suggests) fails; a refresh after `OzonSessionExpired` works, because that error needs an existing file (`playwright_session.py:215`). `bootstrap` also needs a `probe_url` with no Ozon default. Review: fails loudly, workaround is one argument, so LOW. | Pass `ozon_state_path()` when bootstrapping; give `OzonBrowser.bootstrap` a default probe URL. | LOW ✅ OzonBrowser.bootstrap defaults state path and probe |
| 3 ✅ | 8, 10 | `ozon/source.py` `offers` and `specs` each call `fetch_json(product_path)`; one product costs two navigations (serialised, challenge-fragile). OLX caches the same case. | Cache the last product page per id, or add one call returning both. | LOW ✅ one-page cache, 60 s |
| 4 ✅ | 1, 5, 11 | No curl_cffi: OLX and Kimovil build a plain httpx client with no warning (`olx/client.py:59`, `kimovil/client.py:49`). OLX then raises `HTTPStatusError` 403; Kimovil returns None indistinguishable from "no match". `parse_phone` ImportError without selectolax surfaces only at the second step. | Log a warning at construction when `impersonate.available()` is false; check selectolax at construction. | LOW ✅ warning at construction; selectolax check not added (parse_phone ImportError message is enough) |
| 5 ✅ | 8, 9 | `KaspiClient.reviews` default `limit=9`, negative 30, no paging parameter exposed. Same for offers: `KaspiSource.offers` gets the first 32 only (`kaspi/client.py:120`), so a cheaper seller past 32 is missed unless Kaspi sorts by price. | Question: intended cap, or expose `limit`/`page`? | LOW (Question) ✅ decision: keep as is (virtual-me and virtual-rashid both A, medium-high): caps documented in the map |
| 6 ✅ | 3 | `search_all` returns a still-capped slice (width at most 1000 KZT, 1000 results) without a warning at that point (`olx/source.py:184`). Its pages run in one `asyncio.gather` (`olx/source.py:156`): one failed page loses every page fetched. | Log a warning naming the slice; `return_exceptions=True` and log the failed pages. | LOW ✅ warning + return_exceptions |
| 7 ✅ | 2 | Extra Kaspi/OLX clients take the proxy from `client.http.proxy` (`kaspi/source.py:161`, `olx/source.py:231`, `:241`). When that is None, OLX extras get no proxy, and Kaspi extras fall back to `$KASPI_PROXY` (`kaspi/client.py:72`): an injected client without `http`, or `KaspiSource(proxy="")` meant to switch the env proxy off, gives other cities a different proxy than the default city. | Document, or accept `proxy` as a constructor attribute. | LOW ✅ source keeps its proxy arg as fallback |
| 8 ✅ | 11 | Kimovil autocomplete returning HTML with 200 raises `ValueError` from `json()` (`polite_http.py:277`), and a JSON body that is not an object raises `AttributeError` in `parse_autocomplete` (`kimovil/parse.py:61`), while the rest of `lookup` returns None on failure (`kimovil/source.py:60`). | Catch `ValueError` next to `httpx.HTTPError`; check the payload is a dict. | LOW ✅ ValueError caught, non-dict payload ignored |
| 9 ✅ | 7, 8 | `OlxSource._offer` keys the cache by raw `product_id`; an int id misses the str-keyed cache (`olx/source.py:215`). Caches in Kaspi/OLX sources never evict. | `str(product_id)`; note the lifetime in the docstring. | LOW ✅ str(id) + docstring |
| 10 | 6, 7, 8 | Kaspi keeps only the leading digits of a card id (`kaspi/parse.py:31`, `:44-46`), so variant cards "16535441a" and "16535441b" with different `configSku` would share `product_id` "16535441"; `_api_ids` keeps the last one (`kaspi/source.py:94`), and offers, reviews and specs of that variant are served for both. Found in review. Not confirmed: needs a search where two variants of one model show up. | Live run on a fashion query; if confirmed, key by the full card id or by `configSku`. | MEDIUM (Not confirmed) |
| 11 ✅ | 10 | OLX `parse_specs` always sets `seller_type`: "private" when neither `user.seller_type` nor `business` is present (`olx/parse.py:66-70`), also for the empty dict `_offer` returns when detail has no `data` (`olx/source.py:219`). Unknown reads as "private". Found in review. | Omit `seller_type` when neither field is present; return `{}` for an empty offer. | LOW ✅ seller_type omitted when unknown |
| 12 ✅ | 5, 6 | Ozon failures outside `OzonError`: a navigation timeout raises playwright's `TimeoutError`, not retried and not wrapped (`playwright_session.py:205-210`, `_DEAD_RE` at `:75`); a star-rating or seller-rating text that is not a number raises `ValueError` from `float()` and loses the whole page (`ozon/parse.py:101`, `:167`). A caller catching `OzonError` crashes on both. Found in review. | Wrap the timeout in `OzonError` (or retry it); guard the two `float()` calls. | LOW ✅ timeout wrapped in OzonError, float guards |

## Order of work

1. #1 and #10 first (live runs decide them), then #2.
2. #4, #6, #8, #12 share logging and error handling.

## Done when

- [ ] every finding has ✅ or a "keep as is" decision with a reason;
- [ ] Known gaps in the map lists every open HIGH and MEDIUM finding.
