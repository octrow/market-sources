"""Wildberries: search JSON parsing and source wiring (no browser)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from market_sources.wb.parse import currency_of, parse_offer, parse_search
from market_sources.wb.source import WbSource

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "wb_search.json").read_text())
SEARCH_URL = "https://global.wildberries.ru/__internal/u-search/exactmatch/sng/common/v18/search?curr=kzt&dest=84&query=x"


def test_the_currency_comes_from_the_search_request():
    assert currency_of(SEARCH_URL) == "KZT"
    assert currency_of("https://x/search?query=y") == "RUB"


def test_a_search_product_has_its_price_in_whole_tenge_and_its_card_fields():
    first = parse_search(FIXTURE, "KZT")[0]

    assert first.source == "wb"
    assert first.product_id == "22893732"
    assert first.price == 9_280
    assert first.currency == "KZT"
    assert first.brand == "ТЕЛОДВИЖЕНИЯ"
    assert (first.rating, first.review_count) == (4.8, 143179)
    assert first.url == "https://global.wildberries.ru/catalog/22893732/detail.aspx"


def test_a_product_without_a_price_is_kept_unpriced():
    item = {**FIXTURE["products"][0], "sizes": [{"price": {"product": 0}}]}

    assert parse_search({"products": [item]}, "KZT")[0].price is None
    assert parse_offer(item, "KZT") is None


def test_the_offer_names_the_seller_and_the_delivery_day():
    now = datetime(2026, 11, 11, 20, 0, tzinfo=ZoneInfo("Asia/Almaty"))

    offer = parse_offer(FIXTURE["products"][0], "KZT", now)

    assert (offer.seller, offer.merchant_rating, offer.price) == ("ООО СКВТ", 4.8, 9_280)
    assert offer.delivery_date == "2026-11-12"  # 1 + 22 hours


class _Browser:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.closed = False

    async def search_json(self, query: str) -> tuple[str, dict]:
        self.queries.append(query)
        return SEARCH_URL, FIXTURE

    async def aclose(self) -> None:
        self.closed = True


async def test_the_source_answers_offers_from_the_search_that_found_the_product():
    browser = _Browser()
    source = WbSource(browser)

    products = await source.search("худи")
    offers = await source.offers(products[0].product_id)

    assert browser.queries == ["худи"]
    assert len(products) == 3
    assert [o.seller for o in offers] == ["ООО СКВТ"]
    assert await source.offers("unknown") == []
    await source.aclose()
    assert browser.closed
