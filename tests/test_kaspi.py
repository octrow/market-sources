"""Kaspi.kz: pure parsers over minimal captured-shape payloads, plus client wiring.

Fixtures are derived from the branches ``parse.py`` actually reads — no invented
fields.
"""

from __future__ import annotations

import json

import httpx
import pytest
from fakes import LowRng, RecordingSleep, ScriptedTransport

from market_sources.base import RawReviewStats
from market_sources.kaspi.client import BASE_URL, DEFAULT_CITY, USER_AGENT, KaspiClient
from market_sources.kaspi.parse import (
    parse_facets,
    parse_offers,
    parse_review_stats,
    parse_reviews,
    parse_search,
    parse_specs,
)
from market_sources.kaspi.source import NEGATIVE_LIMIT, KaspiSource
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


# Trimmed from a live search card (2026-10-22, "кроссовки мужские"): only the
# fields parse_search reads.
FASHION_CARD = {
    "id": "16535441b",
    "configSku": "121564477",
    "title": "Кроссовки LOOK черный",
    "brand": "LOOK",
    "isBrandOfficialPartner": False,
    "category": ["Обувь", "Мужская обувь", "Мужские кроссовки и кеды"],
    "categoryRu": ["Обувь", "Мужская обувь", "Мужские кроссовки и кеды"],
    "shopLink": "/p/krossovki-look-chernyi-16535441b/",
    "unitSalePrice": 12990,
}


def test_a_fashion_card_keeps_brand_category_and_config_sku():
    [product] = parse_search({"data": {"cards": [FASHION_CARD]}})
    assert product.product_id == "16535441"
    assert product.review_id == "121564477"
    assert product.brand == "LOOK"
    assert product.official_partner is False
    assert product.category_path == ["Обувь", "Мужская обувь", "Мужские кроссовки и кеды"]
    assert product.specs_available is True  # the source fetches specs by configSku


def test_a_config_sku_equal_to_the_id_is_not_repeated():
    [product] = parse_search({"data": {"cards": [card(configSku="123456")]}})
    assert product.review_id is None


def test_a_no_name_brand_is_kept_verbatim():
    [product] = parse_search({"data": {"cards": [card(brand="Без бренда")]}})
    assert product.brand == "Без бренда"


def test_a_card_without_the_new_fields_leaves_them_unknown():
    [product] = parse_search({"data": {"cards": [card()]}})
    assert product.brand is None
    assert product.review_id is None
    assert product.official_partner is None
    assert product.category_path == []


def test_a_malformed_category_or_partner_flag_is_dropped():
    [product] = parse_search(
        {"data": {"cards": [card(categoryRu="Обувь", isBrandOfficialPartner="yes")]}}
    )
    assert product.category_path == []
    assert product.official_partner is None


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


def test_an_offer_without_a_price_is_skipped():
    assert parse_offers({"offers": [{}]}, 999) == []


def test_a_missing_merchant_id_is_none():
    [offer] = parse_offers({"offers": [{"price": 100}]}, 999)
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

    async def search(self, query, page=0, sort="relevance", budget=None):
        self.calls.append(("search", query))
        self.budget = budget
        return {"data": {"cards": [card()]}}

    async def offers(self, product_id):
        self.calls.append(("offers", product_id))
        return {"offers": [{"merchantName": "Alser", "price": 1}]}

    async def reviews(self, product_id, **kwargs):
        self.calls.append(("reviews", product_id))
        self.review_kwargs = kwargs
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


# ------------------------------------------------------- review pros/cons/photos


def test_plus_and_minus_are_kept_as_separate_fields_and_still_merged_into_text():
    comment = {"plus": "Быстрый", "minus": "Дорогой", "text": "Ок"}
    [review] = parse_reviews({"data": [{"comment": comment}]}, "1")
    assert (review.pros, review.cons) == ("Быстрый", "Дорогой")
    assert review.text == "Быстрый Дорогой Ок"


def test_empty_plus_minus_and_no_photo_key_stay_none():
    [review] = parse_reviews({"data": [{"comment": {"plus": "", "text": "Ок"}}]}, "1")
    assert (review.pros, review.cons, review.has_photos) == (None, None, None)


# -------------------------------------------------------------- parse_review_stats


def stats_payload(**overrides) -> dict:
    base = {
        "summary": {
            "global": 4.7,
            "statistic": [{"rate": 5, "count": 80}, {"rate": 1, "count": 3}],
        },
        "groupSummary": [
            {"id": "ALL", "total": 100},
            {"id": "COMMENT", "total": 60},
            {"id": "PICTURE", "total": 12},
            {"id": "POSITIVE", "total": 90},
            {"id": "NEGATIVE", "total": 4},
        ],
    }
    base.update(overrides)
    return base


def test_parse_review_stats_maps_summary_and_group_totals():
    stats = parse_review_stats(stats_payload(), "123456")
    assert stats == RawReviewStats(
        source="kaspi",
        product_id="123456",
        rating_counts={5: 80, 1: 3},
        total=100,
        negative_count=4,
        with_photos_count=12,
        average_rating=4.7,
    )


def test_parse_review_stats_is_none_without_summary_or_group_summary():
    assert parse_review_stats({"data": []}, "1") is None


def test_parse_review_stats_skips_malformed_entries_instead_of_raising():
    payload = {
        "summary": {
            "global": "n/a",
            "statistic": [{"rate": "5", "count": 2}, {"rate": 4}, "junk", {"rate": 3, "count": 1}],
        },
        "groupSummary": [{"id": "ALL", "total": "many"}, {"id": "NEGATIVE", "total": 2}, "junk"],
    }
    stats = parse_review_stats(payload, "1")
    assert stats.rating_counts == {3: 1}
    assert (stats.average_rating, stats.total, stats.negative_count) == (None, None, 2)


# ------------------------------------------------------------- offer delivery


def offer_with_options(delivery_type=None, **options) -> dict:
    payload = {"merchantName": "Alser", "price": 100, "deliveryOptions": options}
    if delivery_type is not None:
        payload["deliveryType"] = delivery_type
    return payload


def test_a_naive_datetime_is_utc_so_late_evening_utc_is_next_day_in_almaty():
    offer_payload = offer_with_options(
        "EXPRESS", EXPRESS={"delivery": "2026-10-13T20:00:00", "deliveryCost": 1500}
    )
    [offer] = parse_offers({"offers": [offer_payload]}, "1")
    assert offer.delivery_date == "2026-10-14"


def test_a_utc_morning_stays_on_the_same_almaty_day():
    [offer] = parse_offers(
        {
            "offers": [
                offer_with_options("EXPRESS", EXPRESS={"delivery": "2026-10-13T10:00:00"})
            ]
        },
        "1",
    )
    assert offer.delivery_date == "2026-10-13"


def test_a_trailing_z_is_utc_too():
    [offer] = parse_offers(
        {"offers": [offer_with_options("EXPRESS", EXPRESS={"delivery": "2026-10-13T20:00:00Z"})]},
        "1",
    )
    assert offer.delivery_date == "2026-10-14"


def test_the_delivery_type_selects_its_option_for_date_and_cost():
    [offer] = parse_offers(
        {
            "offers": [
                offer_with_options(
                    "TO_DOOR",
                    EXPRESS={"delivery": "2026-10-13T10:00:00", "deliveryCost": 1500},
                    TO_DOOR={"delivery": "2026-10-20T10:00:00", "deliveryCost": 0},
                )
            ]
        },
        "1",
    )
    assert (offer.delivery_date, offer.delivery_cost) == ("2026-10-20", 0.0)


def test_without_a_matching_type_express_wins_then_to_door_then_the_first_option():
    express_first = offer_with_options(
        "PICKUP",
        TO_DOOR={"delivery": "2026-10-20T10:00:00", "deliveryCost": 500},
        EXPRESS={"delivery": "2026-10-13T10:00:00", "deliveryCost": 1500},
    )
    to_door_only = offer_with_options(
        None,
        PICKUP={"delivery": "2026-10-15T10:00:00", "deliveryCost": 0},
        TO_DOOR={"delivery": "2026-10-20T10:00:00", "deliveryCost": 500},
    )
    first_only = offer_with_options(
        None, PICKUP={"delivery": "2026-10-15T10:00:00", "deliveryCost": 0}
    )
    picked = parse_offers(
        {"offers": [express_first, to_door_only, first_only]}, "1"
    )
    assert [o.delivery_cost for o in picked] == [1500.0, 500.0, 0.0]
    assert [o.delivery_date for o in picked] == ["2026-10-13", "2026-10-20", "2026-10-15"]


def test_missing_delivery_options_give_no_date_or_cost():
    [offer] = parse_offers({"offers": [{"price": 100, "deliveryDuration": "Сегодня"}]}, "1")
    assert (offer.delivery_date, offer.delivery_cost, offer.delivery) == (
        None,
        None,
        "Сегодня",
    )


def test_garbage_delivery_date_is_none_but_a_valid_cost_is_kept():
    [offer] = parse_offers(
        {
            "offers": [
                offer_with_options(
                    "EXPRESS", EXPRESS={"delivery": "soon", "deliveryCost": "1500"}
                )
            ]
        },
        "1",
    )
    assert (offer.delivery_date, offer.delivery_cost) == (None, 1500.0)


# ------------------------------------------------------------- source reviews_with_stats


async def test_reviews_with_stats_makes_one_call_and_parses_both():
    stub = StubKaspiClient()

    async def reviews(product_id):
        stub.calls.append(("reviews", product_id))
        return stats_payload(data=[{"rating": 5}])

    stub.reviews = reviews
    reviews_list, stats = await KaspiSource(client=stub).reviews_with_stats("123456")
    assert [r.rating for r in reviews_list] == [5.0]
    assert stats.total == 100
    assert [c for c in stub.calls if c[0] == "reviews"] == [("reviews", "123456")]


async def test_reviews_still_returns_only_the_list():
    source = KaspiSource(client=StubKaspiClient())
    assert (await source.reviews("123456"))[0].rating == 5.0


# ------------------------------------------------------------- variant ids (configSku)


async def test_variant_calls_use_the_config_sku_remembered_from_search():
    stub = StubKaspiClient()

    async def search(query, page=0, sort="relevance", budget=None):
        return {"data": {"cards": [FASHION_CARD]}}

    stub.search = search
    source = KaspiSource(client=stub)
    await source.search("кроссовки")
    offers = await source.offers("16535441")
    reviews = await source.reviews("16535441")
    await source.specs("16535441")
    assert stub.calls == [
        ("offers", "121564477"),
        ("reviews", "121564477"),
        ("specs", "121564477"),
    ]
    # Results stay keyed by the id the caller holds.
    assert offers[0].product_id == "16535441"
    assert reviews[0].product_id == "16535441"


async def test_an_unknown_product_id_is_sent_as_is():
    stub = StubKaspiClient()
    await KaspiSource(client=stub).reviews("999")
    assert stub.calls == [("reviews", "999")]


async def test_an_explicit_review_id_wins():
    stub = StubKaspiClient()
    reviews = await KaspiSource(client=stub).reviews("15358806", review_id="130678577")
    assert stub.calls == [("reviews", "130678577")]
    assert reviews[0].product_id == "15358806"


async def test_negative_requests_the_negative_filter_with_a_larger_limit():
    stub = StubKaspiClient()
    source = KaspiSource(client=stub)
    await source.reviews_with_stats("123456", negative=True)
    assert stub.review_kwargs == {"limit": NEGATIVE_LIMIT, "filter": "NEGATIVE"}
    await source.reviews("123456")
    assert stub.review_kwargs == {}


# ------------------------------------------------------------- budget and city


async def test_a_budget_selects_every_price_bucket_up_to_it():
    client, transport = kaspi_client()
    await client.search("x", budget=60_000)
    assert dict(transport.requests[0].url.params)["q"] == (
        ":price:до 10 000 т:price:10 000 - 49 999 т:price:50 000 - 99 999 т"
    )


async def test_no_budget_or_one_above_every_bucket_sends_no_price_filter():
    client, transport = kaspi_client()
    await client.search("x")
    await client.search("x", budget=500_000)
    assert ["q" in dict(r.url.params) for r in transport.requests] == [False, False]


async def test_a_budget_on_a_bucket_edge_includes_that_bucket():
    client, transport = kaspi_client()
    for budget in (49_999, 50_000, -1):
        await client.search("x", budget=budget)
    edges = [dict(r.url.params)["q"].count(":price:") for r in transport.requests]
    assert edges == [2, 3, 1]


async def test_filters_join_the_price_in_q():
    client, transport = kaspi_client()
    await client.search("x", filters=":Dishwashers*Height:менее 75 см")
    await client.search("x", budget=30_000, filters=":Dishwashers*Width:40 см")
    first, second = (dict(r.url.params).get("q") for r in transport.requests)
    assert first == ":Dishwashers*Height:менее 75 см"
    assert second.startswith(":price:") and second.endswith(":Dishwashers*Width:40 см")


def test_parse_facets_maps_titles_to_row_ids():
    payload = {
        "data": {
            "filters": [
                {"title": "Высота", "rows": [{"id": ":D*Height:82 см", "title": "82 см"}]},
                {"title": "Пусто", "rows": []},
            ]
        }
    }
    assert parse_facets(payload) == {"Высота": [(":D*Height:82 см", "82 см")]}
    assert parse_facets({}) == {}


async def test_the_source_remembers_facets_and_passes_page_and_filters():
    class FacetClient(StubKaspiClient):
        async def search(self, query, page=0, sort="relevance", budget=None, filters=""):
            self.page, self.filters = page, filters
            row = {"id": ":D*Height:45 см", "title": "45 см"}
            return {"data": {"cards": [card()], "filters": [{"title": "Высота", "rows": [row]}]}}

    stub = FacetClient()
    source = KaspiSource(client=stub)
    await source.search("x", page=1, filters=":D*Width:40 см")
    assert (stub.page, stub.filters) == (1, ":D*Width:40 см")
    assert source.facets("x") == {"Высота": [(":D*Height:45 см", "45 см")]}
    assert source.facets("other") == {}


async def test_an_injected_client_without_budget_still_searches():
    class OldClient(StubKaspiClient):
        async def search(self, query, page=0, sort="relevance"):
            return {"data": {"cards": [card()]}}

    assert len(await KaspiSource(client=OldClient()).search("x")) == 1


def test_an_unknown_city_name_is_an_error_not_an_id():
    source = KaspiSource(client=StubKaspiClient())
    with pytest.raises(ValueError, match="Алмата"):
        source._client_for_city("Алмата")
    assert source._client_for_city("") is source._client


async def test_the_source_passes_the_budget_to_the_client():
    stub = StubKaspiClient()
    await KaspiSource(client=stub).search("x", budget=30_000)
    assert stub.budget == 30_000


def test_a_city_name_resolves_to_its_kaspi_id():
    source = KaspiSource(client=StubKaspiClient())
    assert source._client_for_city("Алматы") is source._client
    assert source._client_for_city("Шымкент").city == "511010000"


async def test_offers_come_from_the_city_the_product_was_found_in():
    source = KaspiSource(client=StubKaspiClient())
    shymkent = source._city_clients["511010000"] = StubKaspiClient(city="511010000")
    await source.search("x", city="Шымкент")
    await source.offers("123456")
    await source.offers("999")  # never searched: the default city
    assert ("offers", "123456") in shymkent.calls
    assert ("offers", "999") in source._client.calls


async def test_an_explicit_city_wins_for_offers():
    source = KaspiSource(client=StubKaspiClient())
    astana = source._city_clients["710000000"] = StubKaspiClient(city="710000000")
    await source.offers("123456", city="710000000")
    assert astana.calls == [("offers", "123456")]
