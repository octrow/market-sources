"""Ozon.kz: composer-api widget parsers, plus browser/source wiring (no browser)."""

from __future__ import annotations

import json

import pytest
from fakes import FakePage, PageFactory

from market_sources.ozon.browser import BASE_URL, CHALLENGE_WAIT_MS, OzonBrowser, OzonError
from market_sources.ozon.parse import (
    parse_offer,
    parse_reviews,
    parse_search,
    parse_specs,
    price_to_int,
)
from market_sources.ozon.source import OzonSource, product_path, search_path


def page_with(**widgets) -> dict:
    """A composer-api page whose ``widgetStates`` values are JSON strings."""
    return {"widgetStates": {k: json.dumps(v) for k, v in widgets.items()}}


# ----------------------------------------------------------------- price_to_int


@pytest.mark.parametrize(
    ("text", "expected"),
    [("14 589 ₸", 14589), ("1 216 ₸ × 12 мес", 121612), ("", None), (None, None), (123, None)],
)
def test_price_to_int(text, expected):
    assert price_to_int(text) == expected


# ----------------------------------------------------------------- parse_search


def search_tile(**overrides) -> dict:
    base = {
        "sku": 1234567890,
        "mainState": [
            {"id": "name", "textDS": {"text": "Смартфон Xiaomi Redmi Note 13"}},
            {
                "type": "priceV2",
                "priceV2": {
                    "price": [
                        {"text": "1 216 ₸ × 12 мес", "textStyle": "PRICE"},
                    ]
                },
            },
            {
                "type": "priceV2",
                "priceV2": {
                    "price": [
                        {"text": "89 990 ₸", "textStyle": "PRICE"},
                        {"text": "119 990 ₸", "textStyle": "ORIGINAL_PRICE"},
                    ]
                },
            },
            {
                "labelListV2": {
                    "items": [
                        {"icon": "ic_s_star", "text": {"text": "4,8"}},
                        {"text": {"text": "1 204 отзыва"}},
                    ]
                }
            },
        ],
        "action": {"link": "/product/redmi-note-13-1234567890/?advert=x"},
        "tileImage": {"items": [{"image": {"link": "https://cdn.ozone.test/1.jpg"}}]},
    }
    base.update(overrides)
    return base


def test_parse_search_skips_the_installment_price_block():
    [product] = parse_search(page_with(tileGridDesktop={"items": [search_tile()]}))
    assert (product.source, product.product_id) == ("ozon", "1234567890")
    assert product.title == "Смартфон Xiaomi Redmi Note 13"
    assert product.price == 89990.0
    assert product.url == f"{BASE_URL}/product/redmi-note-13-1234567890/"
    assert product.image_url == "https://cdn.ozone.test/1.jpg"
    assert (product.rating, product.review_count) == (4.8, 1204)


def test_a_widget_name_prefix_must_match_exactly():
    """``webPrice`` must not be satisfied by ``webPriceDecreasedCompact``."""
    page = page_with(webPriceDecreasedCompact={"price": "1 000 ₸"})
    assert parse_offer(page, "1") is None


def test_a_suffixed_widget_key_still_matches_its_name():
    page = page_with(**{"tileGridDesktop-3157489-default-1": {"items": [search_tile()]}})
    assert len(parse_search(page)) == 1


def test_tiles_missing_a_name_price_or_sku_are_dropped():
    tiles = [
        search_tile(sku=None, id=None),
        search_tile(mainState=[{"id": "name", "textDS": {"text": "Только имя"}}]),
        search_tile(mainState=[m for m in search_tile()["mainState"] if m.get("id") != "name"]),
        search_tile(),
    ]
    products = parse_search(page_with(tileGridDesktop={"items": tiles}))
    assert [p.product_id for p in products] == ["1234567890"]


def test_an_id_stands_in_for_a_missing_sku():
    [product] = parse_search(page_with(tileGridDesktop={"items": [search_tile(sku=None, id=42)]}))
    assert product.product_id == "42"


def test_an_absolute_tile_link_is_left_alone_and_a_cover_image_is_a_fallback():
    tile = search_tile(
        action={"link": "https://other.test/p/1"},
        tileImage={"coverImage": "https://cdn.test/cover.jpg"},
    )
    [product] = parse_search(page_with(tileGridDesktop={"items": [tile]}))
    assert product.url == "https://other.test/p/1"
    assert product.image_url == "https://cdn.test/cover.jpg"


def test_a_tile_without_a_rating_label():
    tile = search_tile(mainState=[m for m in search_tile()["mainState"] if "labelListV2" not in m])
    [product] = parse_search(page_with(tileGridDesktop={"items": [tile]}))
    assert (product.rating, product.review_count) == (None, None)


def test_parse_search_tolerates_a_missing_or_broken_widget():
    assert parse_search({}) == []
    assert parse_search({"widgetStates": {"tileGridDesktop-1": "not json"}}) == []
    assert parse_search({"widgetStates": {"tileGridDesktop-1": None}}) == []


# ------------------------------------------------------------------ parse_offer


def test_parse_offer_uses_the_card_price_and_the_current_seller():
    page = page_with(
        webPrice={"cardPrice": "84 990 ₸", "price": "89 990 ₸", "isAvailable": True},
        webCurrentSeller={
            "sellerCell": {"centerBlock": {"title": {"text": "MobileWorld"}}},
            "rating": {"title": {"text": "4,7"}},
        },
    )
    offer = parse_offer(page, "1234567890")
    assert (offer.source, offer.product_id, offer.seller) == ("ozon", "1234567890", "MobileWorld")
    assert offer.price == 84990.0
    assert offer.in_stock is True
    assert offer.merchant_rating == 4.7


def test_the_plain_price_is_the_fallback_and_unavailability_is_honored():
    page = page_with(webPrice={"price": "89 990 ₸", "isAvailable": False})
    offer = parse_offer(page, "1")
    assert offer.price == 89990.0
    assert offer.in_stock is False
    assert offer.seller is None
    assert offer.merchant_rating is None


def test_a_seller_widget_title_stands_in_for_a_missing_cell_title():
    page = page_with(
        webPrice={"price": "100 ₸"},
        webCurrentSeller={"title": {"text": "Ozon Продавец"}},
    )
    assert parse_offer(page, "1").seller == "Ozon Продавец"


def test_a_nameless_seller_widget_is_ignored():
    page = page_with(webPrice={"price": "100 ₸"}, webCurrentSeller={"rating": {"title": {}}})
    assert parse_offer(page, "1").seller is None


def test_no_price_widget_or_no_price_means_no_offer():
    assert parse_offer({}, "1") is None
    assert parse_offer(page_with(webPrice={"price": "договорная"}), "1") is None


# ------------------------------------------------------------------ parse_specs


def test_parse_specs_flattens_short_characteristics():
    page = page_with(
        webShortCharacteristics={
            "characteristics": [
                {
                    "title": {"textRs": [{"text": "Бренд"}]},
                    "values": [{"text": "Xiaomi"}],
                },
                {
                    "title": {"textRs": [{"content": "Цвет"}]},
                    "valueRs": [{"text": "чёрный,"}, {"text": "матовый"}],
                },
                {"title": {"textRs": [{"text": "Без значения"}]}, "values": []},
                {"values": [{"text": "Без имени"}]},
            ]
        }
    )
    assert parse_specs(page) == {"Бренд": "Xiaomi", "Цвет": "чёрный, матовый"}


def test_parse_specs_tolerates_a_missing_widget():
    assert parse_specs({}) == {}


# ---------------------------------------------------------------- parse_reviews


def test_parse_reviews_joins_the_content_parts_and_dates_the_review():
    page = page_with(
        webListReviews={
            "reviews": [
                {
                    "content": {
                        "score": 5,
                        "positive": "Быстрый",
                        "negative": "Греется",
                        "comment": "Доволен",
                    },
                    "author": {"firstName": "Дана", "lastName": "К."},
                    "publishedAt": 1767225600,
                }
            ]
        }
    )
    [review] = parse_reviews(page, "1234567890")
    assert review.text == "Быстрый Греется Доволен"
    assert review.rating == 5.0
    assert review.author == "Дана К."
    assert review.created_at == "2026-01-01"


def test_an_anonymous_review_and_an_items_shaped_widget():
    page = page_with(
        webListReviews={"items": [{"content": {}, "isAnonymous": True, "createdAt": None}]}
    )
    [review] = parse_reviews(page, "1")
    assert review.author == "Аноним"
    assert review.rating is None
    assert review.text == ""
    assert review.created_at is None


def test_an_unnamed_non_anonymous_review_has_no_author():
    page = page_with(webListReviews={"reviews": [{"content": {"score": 4.5}}]})
    [review] = parse_reviews(page, "1")
    assert review.author is None
    assert review.rating == 4.5


def test_an_impossible_timestamp_is_dropped_rather_than_raising():
    page = page_with(webListReviews={"reviews": [{"content": {}, "publishedAt": 10**20}]})
    assert parse_reviews(page, "1")[0].created_at is None


def test_parse_reviews_tolerates_a_missing_widget():
    assert parse_reviews({}, "1") == []


# --------------------------------------------------------------- browser wiring


def browser(*pages: FakePage, **kwargs) -> tuple[OzonBrowser, PageFactory]:
    factory = PageFactory(*pages)
    kwargs.setdefault("idle_timeout_s", 0)
    return OzonBrowser(page_factory=factory, **kwargs), factory


def test_the_composer_url_percent_encodes_the_whole_path():
    ozon, _ = browser(FakePage())
    assert ozon.composer_url("/search/?text=iphone 15&from_global=true") == (
        f"{BASE_URL}/api/composer-api.bx/page/json/v2?url="
        "%2Fsearch%2F%3Ftext%3Diphone%2015%26from_global%3Dtrue"
    )


def test_the_base_url_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("OZON_BASE_URL", "https://staging.ozon.test/")
    assert OzonBrowser().base == "https://staging.ozon.test"
    assert OzonBrowser(base_url="https://explicit.test/").base == "https://explicit.test"


async def test_fetch_json_passes_the_challenge_then_decodes_the_body():
    page = FakePage(body=json.dumps({"widgetStates": {}}), title="Ozon — интернет-магазин")
    ozon, factory = browser(page)
    assert await ozon.fetch_json("/search/?text=x") == {"widgetStates": {}}
    assert page.waits == [CHALLENGE_WAIT_MS]  # the challenge got its dwell time
    assert page.gotos[0] == f"{BASE_URL}/"  # home first
    assert "composer-api" in page.gotos[1]


async def test_an_antibot_title_raises_ozon_error():
    ozon, _ = browser(FakePage(title="Доступ ограничен"))
    with pytest.raises(OzonError, match="anti-bot challenge not passed"):
        await ozon.fetch_json("/search/?text=x")


async def test_an_ordinary_title_mentioning_dostup_is_not_a_challenge():
    page = FakePage(body=json.dumps({"ok": 1}), title="Доступные цены — Ozon")
    ozon, _ = browser(page)
    assert await ozon.fetch_json("/search/?text=x") == {"ok": 1}


async def test_a_non_json_body_raises_ozon_error():
    ozon, _ = browser(FakePage(body="<html>challenge</html>", title="Ozon"))
    with pytest.raises(OzonError, match="non-JSON"):
        await ozon.fetch_json("/search/?text=x")


async def test_a_403_relaunches_the_session_once():
    blocked = FakePage(script=[200, 403], name="blocked")
    fresh = FakePage(script=[200, 200], body=json.dumps({"ok": 1}), name="fresh")
    ozon, factory = browser(blocked, fresh)
    assert await ozon.fetch_json("/search/?text=x") == {"ok": 1}
    assert [p.name for p in factory.opened] == ["blocked", "fresh"]


async def test_a_persistent_403_raises_ozon_error():
    ozon, _ = browser(FakePage(script=[200, 403, 200, 403]))
    with pytest.raises(OzonError, match="HTTP 403"):
        await ozon.fetch_json("/search/?text=x")


# --------------------------------------------------------------- source wiring


def test_the_product_path_accepts_a_bare_sku_or_a_full_path():
    assert product_path("1234567890") == "/product/1234567890/"
    assert product_path(" 1234567890 ") == "/product/1234567890/"
    assert product_path("/product/redmi-1234567890") == "/product/redmi-1234567890/"
    assert product_path("/product/redmi-1234567890/") == "/product/redmi-1234567890/"


def test_the_search_path_encodes_the_query():
    assert search_path("iphone 15") == "/search/?text=iphone%2015&from_global=true"


class StubBrowser:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.paths: list[str] = []
        self.closed = False

    async def fetch_json(self, path, retries=1):
        self.paths.append(path)
        return self.payload

    async def aclose(self):
        self.closed = True


async def test_the_source_wires_the_browser_to_the_parsers():
    payload = {
        **page_with(
            tileGridDesktop={"items": [search_tile()]},
            webPrice={"price": "89 990 ₸"},
            webShortCharacteristics={
                "characteristics": [
                    {"title": {"textRs": [{"text": "Бренд"}]}, "values": [{"text": "Xiaomi"}]}
                ]
            },
            webListReviews={"reviews": [{"content": {"score": 5}}]},
        )
    }
    stub = StubBrowser(payload)
    source = OzonSource(browser=stub)
    assert source.name == "ozon"
    assert [p.product_id for p in await source.search("iphone 15")] == ["1234567890"]
    assert (await source.offers("1234567890"))[0].price == 89990.0
    assert (await source.reviews("1234567890"))[0].rating == 5.0
    assert await source.specs("1234567890") == {"Бренд": "Xiaomi"}
    await source.aclose()
    assert stub.paths == [
        "/search/?text=iphone%2015&from_global=true",
        "/product/1234567890/",
        "/product/1234567890/reviews/",
        "/product/1234567890/",
    ]
    assert stub.closed is True


async def test_offers_is_empty_when_the_pdp_has_no_price_widget():
    assert await OzonSource(browser=StubBrowser({})).offers("1") == []


async def test_city_is_accepted_for_parity_and_ignored():
    stub = StubBrowser(page_with(tileGridDesktop={"items": []}))
    await OzonSource(browser=stub).search("x", city="750000000")
    assert stub.paths == ["/search/?text=x&from_global=true"]
