<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/octrow/market-sources/main/assets/readme/hero-dark.ru.svg">
  <img src="https://raw.githubusercontent.com/octrow/market-sources/main/assets/readme/hero-light.ru.svg" width="100%" alt="market-sources: асинхронные скрейперы Kaspi.kz, OLX.kz и Ozon.kz за одним протоколом ProductSource: поиск, предложения, отзывы, характеристики. Рядом с названием анти-бот защита каждого сайта и то, как её проходит адаптер: kaspi.kz проверяет referer, и PoliteClient шлёт referer страницы; olx.kz проверяет TLS-отпечаток, и ImpersonateTransport шлёт отпечаток Chrome через curl_cffi; ozon.kz отдаёт JavaScript-челлендж, и BrowserSession читает его через headless Chromium. Голый запрос httpx получает 403 на всех трёх; адаптеры получают 200 и возвращают RawProduct, RawOffer и RawReview.">
</picture>

<p align="center">
  <a href="https://github.com/octrow/market-sources/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/octrow/market-sources/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://pypi.org/project/market-sources/"><img alt="PyPI" src="https://img.shields.io/pypi/v/market-sources"></a>
  <img alt="Python 3.12+" src="https://img.shields.io/badge/python-3.12%2B-blue">
  <a href="https://github.com/octrow/market-sources/blob/main/LICENSE"><img alt="Лицензия MIT" src="https://img.shields.io/badge/license-MIT-green"></a>
</p>

<p align="center"><a href="https://github.com/octrow/market-sources/blob/main/README.md">English</a> | <b>Русский</b></p>

Асинхронные Python-скрейперы **Kaspi.kz**, **OLX.kz** и **Ozon.kz** за одним протоколом `ProductSource`.
Найти товар, получить предложения всех продавцов с ценой и доставкой, прочитать отзывы и характеристики.
Для тех, кто следит за ценами и предложениями на казахстанских маркетплейсах: мониторинг цен, боты со
скидками, выбор перед покупкой.

Под адаптерами два переиспользуемых слоя: `PoliteClient` — асинхронный HTTP-клиент с ограничением темпа и
повторами, и `BrowserSession` — постоянный headless Chromium для сайтов, которым нужен настоящий браузер.

<p align="center"><img src="https://raw.githubusercontent.com/octrow/market-sources/main/docs/demo.gif" alt="Терминал: examples/kaspi_demo.py ищет на Kaspi.kz «iphone 15» за 1,5 с и выводит пять самых дешёвых из 32 предложений с продавцом, рейтингом и доставкой" width="760"></p>

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

Реальный запуск 2026-10-13 (поиск на Kaspi занял 1.4 с и вернул 12 товаров):

```text
Смартфон Apple iPhone 15 128Gb NanoSIM+eSIM черный | 32 offers, from 379500.0 KZT
```

Каждое предложение — обычная Pydantic-модель:

```python
RawOffer(source='kaspi', product_id='113137790', seller='Kaz_Phones', price=379500.0, currency='KZT',
         delivery='EXPRESS', in_stock=True, merchant_rating=4.8, merchant_review_count=32,
         delivery_date='2026-10-13', delivery_cost=995.0, ...)
```

## Установка

```bash
pip install market-sources                  # Kaspi; для OLX нужен extra impersonate (ниже)
pip install "market-sources[impersonate]"   # + curl_cffi, чтобы WAF OLX.kz пропускал запросы
pip install "market-sources[browser]"       # + playwright, для Ozon.kz
playwright install chromium                 # один раз, для extra browser
```

Через uv: `uv add "market-sources[impersonate,browser]"`. Свежий код с GitHub:
`pip install "git+https://github.com/octrow/market-sources"`.

Python 3.12+. Зависимости — `httpx` и `pydantic`. Необязательные extras импортируются лениво, поэтому
`import market_sources` работает и без них.

## Источники

| источник | откуда данные | что нужно | заметки |
|---|---|---|---|
| `KaspiSource` | внутренний JSON API Kaspi через `PoliteClient` | базовая установка | `search`, `offers`, `reviews`, `reviews_with_stats`, `specs`; прокси через переменную `KASPI_PROXY` |
| `OlxSource` | публичный JSON API OLX | extra `impersonate` | CloudFront отбивает TLS-отпечаток httpx; если стоит `curl_cffi`, клиент сам притворяется Chrome (в запуске выше поиск занял 1.9 с и вернул 63 объявления). OLX отдаёт не больше 1000 результатов на поиск; `search_all(query, min_price, max_price)` делит ценовой диапазон, пока каждый кусок не влезет, и выкачивает все страницы (2180 объявлений за 100–160k ₸ за 40 с) |
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

## Чем это отличается от скрипта на httpx или Scrapy

- **Голый запрос httpx получает 403.** У каждого сайта своя защита: Kaspi проверяет referer, OLX смотрит на
  TLS-отпечаток, Ozon показывает JavaScript-проверку. Адаптеры их уже проходят, а причины записаны ниже,
  так что подбирать их методом тыка не придётся.
- **Scrapy — фреймворк для краулеров** со своим рантаймом, пайплайнами и настройками. Здесь небольшая
  асинхронная библиотека: вызываете её из своего кода (бот, приложение на FastAPI, cron-скрипт) и получаете
  Pydantic-модели.
- **Ограничение темпа встроено, и про него не забудешь.** Параллелизм ограничен внутри клиента, задержки
  случайные, `Retry-After` соблюдается, поэтому `asyncio.gather` по 40 страницам не завалит сайт запросами.
- **Сырые данные без интерпретации.** Модели хранят то, что сказал сайт. Нормализация, пересчёт валют,
  дедупликация и ранжирование остаются вам.

Пользуйтесь аккуратно: не уменьшайте темп по умолчанию, кэшируйте что можно и соблюдайте правила маркетплейсов.

## Принципы устройства

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/octrow/market-sources/main/assets/readme/adapter-dark.ru.svg">
  <img src="https://raw.githubusercontent.com/octrow/market-sources/main/assets/readme/adapter-light.ru.svg" width="100%" alt="Каждый адаптер — три файла: client.py (или browser.py) знает эндпоинты и единственный делает I/O; он передаёт разобранный dict в parse.py — чистые функции, которые возвращают Raw-модели и не импортируют клиент; source.py собирает оба в ProductSource. parse.py тестируется без сети и без моков.">
</picture>

**Каждый адаптер — три файла, и только один из них делает I/O.** `client.py` (или `browser.py`) знает
эндпоинты и ничего не знает о форме данных; `parse.py` — чистые функции над уже разобранными `dict` и
никогда не импортирует клиент; `source.py` собирает их в `ProductSource`. Благодаря этому парсеры
тестируются на сохранённых ответах без сети и без моков.

**Сырое на входе, сырое на выходе.** Модели намеренно повторяют форму источника, а не приводятся к общей.
Нормализация, пересчёт валют, дедупликация и ранжирование — дело потребителя; работа библиотеки заканчивается
на «сайт сказал вот это».

### polite_http

* **Голый User-Agent `python-httpx` получает 403 на всех сайтах из списка.** Браузерный UA — не вежливость
  напоказ, а цена ответа 200. Kaspi вдобавок проверяет `referer`: referer нужной страницы — это *вся* его
  защита от ботов, поэтому `request()` принимает `referer` отдельным аргументом, а не заставляет вызывающий
  код собирать заголовок вручную.
* **Темп считается на логический запрос, а не на попытку.** Одна случайная пауза
  `min_delay_s..max_delay_s` внутри семафора перед первой попыткой; backoff при повторах добавляется сверху.
  `max_delay_s=0` полностью отключает паузы (так тесты остаются быстрыми).
* **Семафор защищает от лавины, и он маленький (2).** `asyncio.gather` по 40 страницам всегда в одной строке
  от вызывающего кода; ограничение параллелизма живёт в клиенте, чтобы никто не мог про него забыть.
* **Повтор никогда не повторяет побочный эффект.** `POST`/`PATCH` по умолчанию не повторяются; если вызывающий
  знает, что его POST — это чтение (эндпоинт предложений Kaspi), он явно передаёт `retry=True`. Это включается
  явно, по одному вызову.
* **404 — это ответ, а не сбой.** Повторяются только `429` и `5xx` (плюс ошибки транспорта). Всё остальное
  сразу поднимается через `raise_for_status()` на первой попытке.
* **`Retry-After` важнее формулы backoff** (в секундах *или* HTTP-датой) и ограничен `backoff_max_s`, чтобы
  враждебный `Retry-After: 86400` не усыпил процесс на сутки.
* **Backoff экспоненциальный, со случайной добавкой и потолком**: `backoff_base_s * 2**attempt` плюс до
  `backoff_jitter` от этой величины, но не больше `backoff_max_s`.
* **`sleep` и `rng` подставляются извне.** Поэтому каждое решение о паузе и backoff проверяется без часов —
  только из-за этого у расписания повторов вообще есть тесты.
* **Один `AsyncClient` на всё.** Все четыре исходные реализации создавали новый `httpx.AsyncClient` на каждый
  запрос и каждый раз выбрасывали пул соединений. Вызывайте `aclose()` (или используйте async context manager).

### playwright_session

* **Импорт никогда не требует playwright.** `playwright` импортируется внутри пути запуска, так что
  `import market_sources` работает на машине только с базовыми зависимостями — потребителю, которому нужны
  лишь Kaspi и OLX, ничего лишнего ставить не надо. Это охраняет тест, который блокирует импорт.
* **Одна страница — значит, одна блокировка.** Каждая навигация меняет общее состояние (cookies, историю,
  DOM, из которого читается JSON), поэтому `fetch_text` идёт строго по очереди за `asyncio.Lock`. Параллелизм
  здесь — ошибка корректности, а не ускорение.
* **НЕ блокируйте через route картинки/css/шрифты.** Антибот-проверка грузит через них свои ресурсы;
  блокировка приносит 403. Это самый дорогой урок, записанный в модуле, и причина, по которой в `LAUNCH_ARGS`
  нет блокировок.
* **Проверка проходится один раз на страницу и запоминается.** `prepare_page` — это хук (открыть главную,
  подождать, убедиться, что заголовок — не стена антибота); успех запоминается до закрытия сессии.
* **Протухшая сессия — полный перезапуск, а не повтор на месте.** `403`/`307` или ошибка «target closed»
  значат, что cookies мертвы; помогает только новый browser+context+page, и только один раз за вызов.
* **Закрытие по простою берёт блокировку и сначала отсоединяется.** Headless Chromium стоит ~300 MB, поэтому
  он закрывается через `idle_timeout_s` и лениво запускается снова. Здесь исправлены две ошибки оригинала:
  задача простоя делала `cancel()` *самой себе*, а потом `await` закрытия (так что `CancelledError` прилетал
  посреди закрытия и процесс браузера оставался жить), и закрывала сессию без блокировки навигации (то есть
  могла выдернуть страницу из-под идущей навигации).

## Происхождение

Выделено из проекта автора `buy-smarter` (`src/buysmarter/sources/`). Устройство polite-HTTP и
browser-session опирается ещё на три реализации того же подхода в более ранних скрейперах автора.

### Отличия от исходного кода

| изменение | причина |
|---|---|
| `PoliteClient` вместо `httpx.AsyncClient()` на каждый запрос | пул соединений переиспользуется, а не выбрасывается после каждого вызова; заголовки и темп те же |
| повторы с экспоненциальным backoff и случайной добавкой, учитывают `Retry-After` | оригиналы ждали фиксированные `1.0 * (attempt+1)` и игнорировали `Retry-After` — именно этот пробел отметил аудит |
| POST `offers` у Kaspi передаёт `retry=True` | сохраняет поведение оригинала «повторять любой метод» при новом умолчании «только идемпотентные» |
| `KASPI_PROXY` / `OZON_BASE_URL` читаются из окружения | убрана зависимость от `config.get_settings()` из buy-smarter; по-прежнему переопределяется для каждого экземпляра |
| `BrowserSession` + хук `prepare_page` | в `OzonBrowser` осталось только знание о сайте (проверка + URL composer); механика сессии общая для любого будущего источника на playwright |
| мёртвый браузер определяется по тексту ошибки | больше не нужен экземпляр `playwright.Error`, поэтому автомат состояний тестируется с поддельной страницей |
| сообщение `BrowserSessionError` — `HTTP {status} for {url}` | было `Ozon returned HTTP {status} for {path}`; принадлежность сайту по-прежнему несёт класс (`OzonError`) |
| у каждого адаптера появился `aclose()` | теперь есть пул/браузер, которые надо закрыть; оригиналам нечего было освобождать, кроме браузера Ozon |

## Тесты

`pytest` — без сети, без браузера, без часов. HTTP идёт через двойник `httpx.AsyncBaseTransport` (не `respx` —
на одну зависимость меньше), браузер — через подставленный `page_factory`, время — через подставленные
`sleep` / `rng`. Фикстуры парсеров — минимальные dict, собранные из тех веток, которые `parse.py` реально читает.

```bash
uv run pytest
```

## Как помочь

См. [CONTRIBUTING.ru.md](https://github.com/octrow/market-sources/blob/main/CONTRIBUTING.ru.md). Об уязвимостях сообщайте приватно: [SECURITY.ru.md](https://github.com/octrow/market-sources/blob/main/SECURITY.ru.md).

## Лицензия

[MIT](https://github.com/octrow/market-sources/blob/main/LICENSE)
