# Источники, протокол и модули

## Источники

| источник | откуда данные | что нужно | заметки |
|---|---|---|---|
| `KaspiSource` | внутренний JSON API Kaspi через `PoliteClient` | базовая установка | `search`, `offers`, `reviews`, `reviews_with_stats`, `specs`; прокси через переменную `KASPI_PROXY`. `search(query, city, budget)`: Kaspi фильтрует цену только фиксированными диапазонами, поэтому бюджет 15 000 ₸ вернёт карточки до 49 999 ₸; точный фильтр за вами. `search(page=1)` — вторая страница (без логина Kaspi отдаёт страницы 0 и 1); `facets(query, city)` показывает фасеты последнего поиска, а id их строк передаются обратно в `search(filters=...)` (`":Dishwashers*Height:менее 75 см"`). `offers` спрашивает город, в котором товар нашёлся |
| `OlxSource` | публичный JSON API OLX | extra `impersonate` | CloudFront отбивает TLS-отпечаток httpx; если стоит `curl_cffi`, клиент сам притворяется Chrome (в запуске выше поиск занял 1.9 с и вернул 63 объявления). OLX отдаёт не больше 1000 результатов на поиск; `search_all(query, min_price, max_price)` делит ценовой диапазон, пока каждый кусок не влезет, и выкачивает все страницы (2180 объявлений за 100–160k ₸ за 40 с). `search(region=True)` ищет по всей области города, вместе с окрестными сёлами. `search_cheapest(query, category_id=..., limit=200)` отдаёт объявления от дешёвых к дорогим, без бесплатных: в порядке релевантности видно только ~50 из сотен объявлений по широкому запросу. `category_of(product_id)` возвращает id категории объявления из поиска |
| `OzonSource` | composer API Ozon, читается через настоящий браузер | extra `browser` | сессия сохраняется в `OZON_STATE_PATH` (по умолчанию `~/.config/market_sources/ozon_state.json`); `OZON_BASE_URL` меняет хост |
| `KimovilSource` | JSON автодополнения kimovil.com + серверная страница характеристик | extra `kimovil` | не `ProductSource`, а справочник характеристик. `lookup(title)` превращает заголовок с маркетплейса («Смартфон Samsung Galaxy S24 Ultra 12/256GB») в характеристики модели и индекс Kimovil (0–100). Cloudflare ставит челлендж headless Chromium, curl_cffi получает страницу |

Все источники реализуют один протокол, так что вызывающему коду всё равно, с каким сайтом он работает:

```python
class ProductSource(Protocol):
    async def search(self, query: str, city: str | None = None) -> list[RawProduct]: ...
    async def offers(self, product_id: str) -> list[RawOffer]: ...
    async def reviews(self, product_id: str) -> list[RawReview]: ...
    async def specs(self, product_id: str) -> dict[str, str]: ...
```

`city` принимает id площадки или название города: `market_sources.cities` знает 21 город (по-русски или
латиницей) и их id на Kaspi и OLX (`kaspi_city_id("Шымкент")` → `"511010000"`). Ozon город не учитывает.

## Модули

| модуль | что это |
|---|---|
| `market_sources.base` | протокол `ProductSource` + `RawProduct` / `RawOffer` / `RawReview` / `RawReviewStats` |
| `market_sources.polite_http` | `PoliteClient`: семафор + случайная задержка + браузерные заголовки + прокси + повторы с backoff |
| `market_sources.impersonate` | `ImpersonateTransport`: транспорт httpx, который шлёт запросы через `curl_cffi` с TLS-отпечатком Chrome |
| `market_sources.playwright_session` | `BrowserSession`: ленивый постоянный Chromium, последовательная навигация, ready-хук, перезапуск при сбое |
| `market_sources.kaspi` | `KaspiSource` / `KaspiClient` / чистые `parse_*`: внутренний JSON API |
| `market_sources.olx` | `OlxSource` / `OlxClient` / чистые `parse_*`: публичный JSON API |
| `market_sources.ozon` | `OzonSource` / `OzonBrowser` / чистые `parse_*`: composer API через браузер |
| `market_sources.kimovil` | `KimovilSource` / `KimovilClient` / чистые `model_query`, `best_match`, `parse_phone` |

Демо `examples/kaspi_demo.py` в терминале: [docs/demo.gif](https://raw.githubusercontent.com/octrow/market-sources/main/docs/demo.gif).
