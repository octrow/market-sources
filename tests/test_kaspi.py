"""Kaspi.kz: pure parsers over minimal captured-shape payloads, plus client wiring.

Fixtures are derived from the branches ``parse.py`` actually reads — no invented
fields.
"""

from __future__ import annotations

import json

import httpx
import pytest
from fakes import LowRng, RecordingSleep, ScriptedTransport

from market_sources.kaspi.client import BASE_URL, DEFAULT_CITY, USER_AGENT, KaspiClient
from market_sources.kaspi.parse import parse_offers, parse_reviews, parse_search, parse_specs
from market_sources.kaspi.source import KaspiSource
from market_sources.polite_http import PoliteClient

# ----------------------------------------------------------------- parse_search


def card(**overrides) -> dict:
    base = {
        "id": "123456",
        "title": "Смартфон Apple iPhone 15 128Gb",
        "unitSalePrice": 399990,
        "shopLink": "/p/smartfon-apple-iphone-15-123456/",
        "previewImages": [{"medium": "https://img.test/m.jpg", "large": "https://img.test/l.jpg"}],
        "rating": 4.8,
        "reviewsQuantity": 271,
    }
    base.update(overrides)
    return base


def test_parse_search_maps_a_card():
    [product] = parse_search({"data": {"cards": [card()]}})
    assert product.source == "kaspi"
    assert product.product_id == "123456"
    assert product.title == "Смартфон Apple iPhone 15 128Gb"
    assert product.price == 399990.0
    assert product.url == f"{BASE_URL}/shop/p/smartfon-apple-iphone-15-123456/"
    assert product.image_url == "https://img.test/m.jpg"
    assert product.rating == 4.8
    assert product.review_count == 271
    assert product.specs_available is True


def test_parse_search_falls_back_to_unit_price():
    [product] = parse_search({"data": {"cards": [card(unitSalePrice=None, unitPrice=100)]}})
    assert product.price == 100.0


def test_parse_search_handles_a_missing_price():
    [product] = parse_search({"data": {"cards": [card(unitSalePrice=None)]}})
    assert product.price is None


def test_a_variant_letter_suffix_strips_the_id_and_flags_no_specs():
    [product] = parse_search({"data": {"cards": [card(id="16540138b")]}})
    assert product.product_id == "16540138"
    assert product.specs_available is False


def test_a_non_numeric_id_falls_back_to_the_slug():
    [product] = parse_search(
        {"data": {"cards": [card(id="abc", shopLink="/p/krossovki-seryi-16540138b/")]}}
    )
    assert product.product_id == "16540138"


def test_cards_without_an_id_or_title_are_skipped():
    payload = {
        "data": {
            "cards": [
                card(id=None, shopLink="/p/no-digits/"),
                card(title=None),
                card(id="777", shopLink=None),
            ]
        }
    }
    assert [p.product_id for p in parse_search(payload)] == ["777"]


def test_an_absolute_shop_link_is_left_alone():
    [product] = parse_search({"data": {"cards": [card(shopLink="https://other.test/x-123456/")]}})
    assert product.url == "https://other.test/x-123456/"


def test_a_non_product_path_is_only_prefixed_with_the_host():
    [product] = parse_search({"data": {"cards": [card(shopLink="/shop/p/x-123456/")]}})
    assert product.url == f"{BASE_URL}/shop/p/x-123456/"


def test_image_falls_through_medium_large_small():
    [only_small] = parse_search({"data": {"cards": [card(previewImages=[{"small": "s.jpg"}])]}})
    assert only_small.image_url == "s.jpg"
    [none] = parse_search({"data": {"cards": [card(previewImages=[])]}})
    assert none.image_url is None


def test_all_image_urls_preserved():
    [product] = parse_search(
        {
            "data": {
                "cards": [
                    card(
                        previewImages=[
                            {"medium": "https://img.test/1m.jpg"},
                            {"medium": "https://img.test/2m.jpg", "large": "https://img.test/2l.jpg"},
                            {"large": "https://img.test/3l.jpg"},
                        ]
                    )
                ]
            }
        }
    )
    assert product.image_urls == [
        "https://img.test/1m.jpg",
        "https://img.test/2m.jpg",
        "https://img.test/3l.jpg",
    ]
    assert product.image_url == "https://img.test/1m.jpg"


def test_no_photos_empty_list():
    [product] = parse_search({"data": {"cards": [card(previewImages=[])]}})
    assert product.image_urls == []
    assert product.image_url is None


def test_parse_search_tolerates_an_empty_payload():
    assert parse_search({}) == []
    assert parse_search({"data": None}) == []


# ----------------------------------------------------------------- parse_offers


def test_parse_offers_maps_a_merchant_offer():
    payload = {
        "offers": [
            {
                "merchantName": "Alser",
                "price": 389990,
                "deliveryDuration": "Доставка сегодня",
                "merchantId": 12345,
                "merchantRating": 4.6,
                "merchantReviewsQuantity": 890,
            }
        ]
    }
    [offer] = parse_offers(payload, "123456")
    assert (offer.source, offer.product_id, offer.seller) == ("kaspi", "123456", "Alser")
    assert offer.price == 389990.0
    assert offer.delivery == "Доставка сегодня"
    assert offer.merchant_id == "12345"
    assert (offer.merchant_rating, offer.merchant_review_count) == (4.6, 890)


def test_a_missing_offer_price_becomes_zero_and_a_missing_merchant_id_is_none():
    [offer] = parse_offers({"offers": [{}]}, 999)
    assert offer.price == 0.0
    assert offer.merchant_id is None
    assert offer.product_id == "999"


def test_parse_offers_tolerates_an_empty_payload():
    assert parse_offers({}, "1") == []


# ---------------------------------------------------------------- parse_reviews


def test_parse_reviews_joins_the_comment_parts():
    payload = {
        "data": [
            {
                "rating": 5,
                "author": "Айдана",
                "date": "2026-01-02",
                "comment": {"plus": "Быстрый", "minus": "Дорогой", "text": "Рекомендую"},
            }
        ]
    }
    [review] = parse_reviews(payload, "123456")
    assert review.text == "Быстрый Дорогой Рекомендую"
    assert (review.rating, review.author, review.created_at) == (5.0, "Айдана", "2026-01-02")


def test_a_review_with_no_comment_gets_empty_text():
    [review] = parse_reviews({"data": [{"rating": 4}]}, "1")
    assert review.text == ""
    assert review.author is None


def test_only_the_present_comment_parts_are_joined():
    [review] = parse_reviews({"data": [{"comment": {"minus": "Шумный"}}]}, "1")
    assert review.text == "Шумный"


# ------------------------------------------------------------------ parse_specs


def test_parse_specs_flattens_visible_features_across_groups():
    payload = {
        "specifications": [
            {
                "name": "Общие",
                "features": [
                    {"name": "Бренд", "featureValues": [{"value": "Apple"}]},
                    {"name": "Цвет", "featureValues": [{"value": "чёрный"}, {"value": "матовый"}]},
                    {"name": "Скрытая", "visible": False, "featureValues": [{"value": "x"}]},
                    {"name": "Пустая", "featureValues": []},
                    {"featureValues": [{"value": "безымянная"}]},
                ],
            },
            {"name": "Экран", "features": [{"name": "Диагональ", "featureValues": [{"value": 6.1}]}]},
        ]
    }
    assert parse_specs(payload) == {
        "Бренд": "Apple",
        "Цвет": "чёрный; матовый",
        "Диагональ": "6.1",
    }


def test_parse_specs_tolerates_an_empty_payload():
    assert parse_specs({}) == {}
    assert parse_specs({"specifications": [{"name": "x"}]}) == {}


# --------------------------------------------------------------- client wiring


def kaspi_client(script=None, **kwargs) -> tuple[KaspiClient, ScriptedTransport]:
    transport = ScriptedTransport(script)
    http = PoliteClient(
        user_agent=USER_AGENT,
        headers={"accept": "application/json, text/*", "x-requested-with": "XMLHttpRequest"},
        transport=transport,
        sleep=RecordingSleep(),
        rng=LowRng(),
        min_delay_s=0,
        max_delay_s=0,
    )
    return KaspiClient(http=http, **kwargs), transport


async def test_search_hits_the_filters_endpoint_with_city_and_referer():
    client, transport = kaspi_client()
    await client.search("iphone 15", page=2, sort="price")
    request = transport.requests[0]
    assert request.method == "GET"
    assert str(request.url).startswith(f"{BASE_URL}/yml/product-view/pl/filters?")
    params = dict(request.url.params)
    assert params == {
        "text": "iphone 15",
        "page": "2",
        "c": DEFAULT_CITY,
        "ui": "d",
        "sort": "price",
    }
    assert request.headers["referer"] == f"{BASE_URL}/shop/search/"
    assert request.headers["x-ks-city"] == DEFAULT_CITY
    assert request.headers["cookie"] == f"kaspi.storefront.cookie.city={DEFAULT_CITY}"
    assert request.headers["user-agent"] == USER_AGENT
    assert request.headers["accept"] == "application/json, text/*"
    assert request.headers["x-requested-with"] == "XMLHttpRequest"


async def test_offers_posts_the_body_with_a_product_referer():
    client, transport = kaspi_client()
    await client.offers("123456", limit=8, page=1)
    request = transport.requests[0]
    assert request.method == "POST"
    assert str(request.url) == f"{BASE_URL}/yml/offer-view/offers/123456"
    assert json.loads(request.content) == {
        "cityId": DEFAULT_CITY,
        "id": "123456",
        "merchantUID": "",
        "limit": 8,
        "page": 1,
        "sort": True,
        "installationId": "-1",
    }
    assert request.headers["referer"] == f"{BASE_URL}/shop/p/-123456/?c={DEFAULT_CITY}"


async def test_the_offers_post_opts_into_retries():
    """It is a read despite being a POST, so the 5xx retry must still apply."""
    client, transport = kaspi_client([httpx.Response(503), httpx.Response(200, json={})])
    await client.offers("123456")
    assert len(transport.requests) == 2


async def test_reviews_and_specs_endpoints():
    client, transport = kaspi_client()
    await client.reviews("123456", limit=3)
    await client.specs("123456")
    reviews_req, specs_req = transport.requests
    assert str(reviews_req.url).startswith(
        f"{BASE_URL}/yml/review-view/api/v1/reviews/product/123456?"
    )
    assert dict(reviews_req.url.params) == {
        "filter": "COMMENT",
        "sort": "POPULARITY",
        "limit": "3",
        "withAgg": "true",
    }
    assert str(specs_req.url) == f"{BASE_URL}/yml/content/item/product/123456?c={DEFAULT_CITY}"


async def test_a_non_default_city_reaches_headers_params_and_referer():
    client, transport = kaspi_client(city="710000000")
    await client.search("x")
    await client.specs("1")
    assert dict(transport.requests[0].url.params)["c"] == "710000000"
    assert transport.requests[1].headers["x-ks-city"] == "710000000"
    assert transport.requests[1].headers["referer"].endswith("?c=710000000")


def test_the_proxy_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("KASPI_PROXY", "http://residential.test:3128")
    assert KaspiClient().http.proxy == "http://residential.test:3128"
    monkeypatch.delenv("KASPI_PROXY")
    assert KaspiClient().http.proxy is None
    assert KaspiClient(proxy="http://explicit.test").http.proxy == "http://explicit.test"


def test_the_pacing_defaults_match_the_original():
    http = KaspiClient().http
    assert (http.max_concurrency, http.min_delay_s, http.max_delay_s) == (2, 0.5, 1.5)
    assert (http.timeout_s, http.max_retries) == (10.0, 1)


# --------------------------------------------------------------- source wiring


class StubKaspiClient:
    def __init__(self, city: str = DEFAULT_CITY) -> None:
        self.city = city
        self.calls: list[tuple[str, object]] = []

    async def search(self, query, page=0, sort="relevance"):
        self.calls.append(("search", query))
        return {"data": {"cards": [card()]}}

    async def offers(self, product_id):
        self.calls.append(("offers", product_id))
        return {"offers": [{"merchantName": "Alser", "price": 1}]}

    async def reviews(self, product_id):
        self.calls.append(("reviews", product_id))
        return {"data": [{"rating": 5}]}

    async def specs(self, product_id):
        self.calls.append(("specs", product_id))
        return {"specifications": [{"features": [{"name": "Бренд", "featureValues": [{"value": "A"}]}]}]}

    async def aclose(self):
        self.calls.append(("aclose", None))


async def test_the_source_wires_client_to_parsers():
    stub = StubKaspiClient()
    source = KaspiSource(client=stub)
    assert source.name == "kaspi"
    assert [p.product_id for p in await source.search("iphone")] == ["123456"]
    assert (await source.offers("123456"))[0].seller == "Alser"
    assert (await source.reviews("123456"))[0].rating == 5.0
    assert await source.specs("123456") == {"Бренд": "A"}
    await source.aclose()
    assert [c[0] for c in stub.calls] == ["search", "offers", "reviews", "specs", "aclose"]


async def test_a_matching_city_reuses_the_injected_client():
    stub = StubKaspiClient()
    source = KaspiSource(client=stub)
    await source.search("x", city=DEFAULT_CITY)
    await source.search("x", city=None)
    assert len(stub.calls) == 2


def test_a_different_city_builds_a_separate_client():
    source = KaspiSource(client=StubKaspiClient())
    other = source._client_for_city("710000000")
    assert isinstance(other, KaspiClient)
    assert other.city == "710000000"


async def test_a_city_client_is_cached_and_cleared_by_aclose():
    source = KaspiSource(client=StubKaspiClient())
    first = source._client_for_city("710000000")
    assert source._client_for_city("710000000") is first  # cached, not respawned
    await source.aclose()  # closes default + every city pool without raising
    assert source._city_clients == {}      # cache cleared, no leaked pools left


def test_source_passes_proxy_through_to_the_default_client(monkeypatch):
    monkeypatch.delenv("KASPI_PROXY", raising=False)
    source = KaspiSource(proxy="http://p.test:3128")
    assert source._client.http.proxy == "http://p.test:3128"
