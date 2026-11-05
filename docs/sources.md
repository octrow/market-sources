# Sources, protocol and modules

## Sources

| source | how it gets data | needs | notes |
|---|---|---|---|
| `KaspiSource` | Kaspi's internal JSON API over `PoliteClient` | base install | `search`, `offers`, `reviews`, `reviews_with_stats`, `specs`; `KASPI_PROXY` env var for a proxy. `search(query, city, budget)`: Kaspi filters price only by fixed buckets, so a budget of 15 000 ₸ returns cards up to 49 999 ₸; filter exactly yourself. `search(page=1)` is the second page (Kaspi answers pages 0 and 1 without a login); `facets(query, city)` lists the last search's facets, and their row ids go back as `search(filters=...)` (`":Dishwashers*Height:менее 75 см"`). `offers` asks the city the product was found in |
| `OlxSource` | OLX's public JSON API | `impersonate` extra | CloudFront rejects httpx's TLS fingerprint; with `curl_cffi` installed the client impersonates Chrome automatically (a search took 1.9 s and returned 63 listings in the run above). OLX caps a search at 1000 results; `search_all(query, min_price, max_price)` splits the price range until each slice fits and fetches every page (2180 listings for 100k–160k ₸ in 40 s). `search(region=True)` covers the city's whole oblast, villages around it too. `search_cheapest(query, category_id=..., limit=200)` returns listings cheapest first, free ones skipped: the relevance order shows only ~50 of a broad query's hundreds. `category_of(product_id)` gives the category id of a listing a search returned |
| `OzonSource` | Ozon's composer API, read through a real browser | `browser` extra | session saved to `OZON_STATE_PATH` (default `~/.config/market_sources/ozon_state.json`); `OZON_BASE_URL` overrides the host |
| `KimovilSource` | kimovil.com autocomplete JSON + server-rendered specs page | `kimovil` extra | not a `ProductSource`: a spec reference. `lookup(title)` turns a marketplace title ("Смартфон Samsung Galaxy S24 Ultra 12/256GB") into the model's specs and Kimovil index (0–100). Cloudflare challenges headless Chromium; curl_cffi gets the page |

Every source implements the same protocol, so consumer code doesn't care which site it talks to:

```python
class ProductSource(Protocol):
    async def search(self, query: str, city: str | None = None) -> list[RawProduct]: ...
    async def offers(self, product_id: str) -> list[RawOffer]: ...
    async def reviews(self, product_id: str) -> list[RawReview]: ...
    async def specs(self, product_id: str) -> dict[str, str]: ...
```

`city` takes the site's own id or a city name: `market_sources.cities` maps 21 cities (Russian or Latin
names) to Kaspi and OLX ids (`kaspi_city_id("Шымкент")` → `"511010000"`). Ozon ignores the city.

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
| `market_sources.kimovil` | `KimovilSource` / `KimovilClient` / pure `model_query`, `best_match`, `parse_phone` |

Terminal demo of `examples/kaspi_demo.py`: [docs/demo.gif](https://raw.githubusercontent.com/octrow/market-sources/main/docs/demo.gif).
