"""OLX.kz: pure parsers over minimal captured-shape payloads, plus client wiring."""

from __future__ import annotations

from fakes import LowRng, RecordingSleep, ScriptedTransport
import httpx

from market_sources.olx.client import BASE_URL, DEFAULT_CITY_ID, USER_AGENT, OlxClient
from market_sources.olx.parse import parse_offer, parse_search, parse_specs
from market_sources.olx.source import OlxSource
from market_sources.polite_http import PoliteClient


def listing(**overrides) -> dict:
    base = {
        "id": 987654,
        "title": "iPhone 13 128Gb",
        "url": "https://www.olx.kz/d/obyavlenie/iphone-13-ID123.html",
        "params": [
            {"key": "price", "value": {"value": 210000}},
            {"key": "state", "value": {"key": "used", "label": "Б/у"}},
        ],
        "photos": [
            {"link": "https://img.olx.test/{width}x{height}.jpg", "width": 1080, "height": 720}
        ],
        "location": {"city": {"name": "Алматы"}},
        "user": {"id": 555, "name": "Асхат"},
    }
    base.update(overrides)
    return base


# ----------------------------------------------------------------- parse_search


def test_parse_search_maps_a_listing():
    [product] = parse_search({"data": [listing()]})
    assert (product.source, product.product_id) == ("olx", "987654")
    assert product.title == "iPhone 13 128Gb"
    assert product.price == 210000
    assert product.url == "https://www.olx.kz/d/obyavlenie/iphone-13-ID123.html"
    assert product.image_url == "https://img.olx.test/1080x720.jpg"


def test_photo_dimensions_fall_back_to_defaults():
    [product] = parse_search(
        {"data": [listing(photos=[{"link": "https://i.test/{width}x{height}.jpg"}])]}
    )
    assert product.image_url == "https://i.test/800x600.jpg"


def test_a_photo_without_a_link_and_no_photos_at_all():
    [no_link] = parse_search({"data": [listing(photos=[{"width": 10}])]})
    assert no_link.image_url is None
    [no_photos] = parse_search({"data": [listing(photos=[])]})
    assert no_photos.image_url is None


def test_listings_without_an_id_or_title_are_skipped():
    payload = {"data": [listing(id=None), listing(title=None), listing(id=1)]}
    assert [p.product_id for p in parse_search(payload)] == ["1"]


def test_a_non_numeric_price_param_becomes_none():
    [product] = parse_search(
        {"data": [listing(params=[{"key": "price", "value": {"value": "договорная"}}])]}
    )
    assert product.price is None


def test_a_price_param_whose_value_is_not_a_dict_is_ignored():
    [product] = parse_search({"data": [listing(params=[{"key": "price", "value": "x"}])]})
    assert product.price is None


def test_parse_search_tolerates_an_empty_payload():
    assert parse_search({}) == []
    assert parse_search({"data": None}) == []


# ------------------------------------------------------------------ parse_offer


def test_parse_offer_builds_one_offer_from_the_ad():
    [offer] = parse_offer(listing())
    assert (offer.source, offer.product_id, offer.seller) == ("olx", "987654", "Асхат")
    assert offer.price == 210000.0
    assert offer.city == "Алматы"
    assert offer.merchant_id == "555"


def test_a_company_name_stands_in_for_a_missing_user_name():
    [offer] = parse_offer(listing(user={"company_name": "TechShop"}))
    assert offer.seller == "TechShop"
    assert offer.merchant_id is None


def test_a_priceless_ad_yields_no_offer():
    assert parse_offer(listing(params=[])) == []
    assert parse_offer({}) == []


# ------------------------------------------------------------------ parse_specs


def test_parse_specs_surfaces_condition_seller_type_and_city():
    assert parse_specs(listing()) == {
        "condition": "Б/у",
        "seller_type": "private",
        "city": "Алматы",
    }


def test_a_state_param_without_a_label_falls_back_to_the_key():
    specs = parse_specs(listing(params=[{"key": "state", "value": {"key": "new"}}]))
    assert specs["condition"] == "new"


def test_the_business_flag_makes_the_seller_type_business():
    assert parse_specs(listing(business=True))["seller_type"] == "business"


def test_an_explicit_seller_type_wins_over_the_business_flag():
    listing_data = listing(user={"seller_type": "shop"}, business=True)
    assert parse_specs(listing_data)["seller_type"] == "shop"


def test_specs_of_an_empty_offer_still_reports_a_seller_type():
    assert parse_specs({}) == {"seller_type": "private"}


# --------------------------------------------------------------- client wiring


def olx_client(script=None, **kwargs) -> tuple[OlxClient, ScriptedTransport]:
    transport = ScriptedTransport(script)
    http = PoliteClient(
        user_agent=USER_AGENT,
        transport=transport,
        sleep=RecordingSleep(),
        rng=LowRng(),
        min_delay_s=0,
        max_delay_s=0,
    )
    return OlxClient(http=http, **kwargs), transport


async def test_search_hits_the_public_offers_api_with_browser_headers():
    client, transport = olx_client()
    await client.search("iphone 13")
    request = transport.requests[0]
    assert str(request.url).startswith(f"{BASE_URL}/api/v1/offers/?")
    assert dict(request.url.params) == {
        "offset": "0",
        "limit": "50",
        "query": "iphone 13",
        "city_id": DEFAULT_CITY_ID,
    }
    assert request.headers["user-agent"] == USER_AGENT
    assert request.headers["accept"] == "application/json, text/plain, */*"
    assert request.headers["accept-language"] == "ru-RU,ru;q=0.9,en;q=0.8"
    assert "referer" not in request.headers  # OLX has no referer gate


async def test_a_budget_becomes_a_server_side_price_filter():
    client, transport = olx_client()
    await client.search("iphone", budget=250000, offset=40, limit=20)
    params = dict(transport.requests[0].url.params)
    assert params["filter_float_price:to"] == "250000"
    assert (params["offset"], params["limit"]) == ("40", "20")


async def test_detail_hits_the_per_offer_endpoint():
    client, transport = olx_client()
    await client.detail("987654")
    assert str(transport.requests[0].url) == f"{BASE_URL}/api/v1/offers/987654/"


async def test_a_5xx_is_retried_once():
    client, transport = olx_client([httpx.Response(502), httpx.Response(200, json={})])
    await client.search("x")
    assert len(transport.requests) == 2


def test_the_pacing_defaults_match_the_original():
    http = OlxClient().http
    assert (http.max_concurrency, http.min_delay_s, http.max_delay_s) == (2, 0.5, 1.5)
    assert (http.timeout_s, http.max_retries) == (15.0, 1)


# --------------------------------------------------------------- source wiring


class StubOlxClient:
    def __init__(self, pages: list[dict] | None = None, city_id: str = DEFAULT_CITY_ID) -> None:
        self.city_id = city_id
        self.pages = pages or [{"data": [listing()]}]
        self.searches: list[dict] = []
        self.details: list[str] = []

    async def search(self, query, offset=0, limit=50, budget=None):
        self.searches.append({"query": query, "offset": offset, "budget": budget})
        index = min(len(self.searches) - 1, len(self.pages) - 1)
        return self.pages[index]

    async def detail(self, offer_id):
        self.details.append(offer_id)
        return {"data": listing(id=int(offer_id))}

    async def aclose(self):
        pass


async def test_the_source_serves_offers_and_specs_from_the_search_cache():
    stub = StubOlxClient()
    source = OlxSource(client=stub)
    assert source.name == "olx"
    [product] = await source.search("iphone 13", budget=250000)
    assert stub.searches[0]["budget"] == 250000
    [offer] = await source.offers(product.product_id)
    assert offer.price == 210000.0
    assert (await source.specs(product.product_id))["condition"] == "Б/у"
    assert stub.details == []  # never paid for a detail call
    await source.aclose()


async def test_an_unseen_product_falls_back_to_a_detail_fetch():
    stub = StubOlxClient()
    source = OlxSource(client=stub)
    [offer] = await source.offers("111")
    assert offer.product_id == "111"
    assert stub.details == ["111"]
    await source.offers("111")
    assert stub.details == ["111"]  # cached after the first fetch


async def test_olx_never_has_reviews():
    assert await OlxSource(client=StubOlxClient()).reviews("1") == []


async def test_the_source_paginates_once_when_the_first_page_is_capped():
    first = {"data": [listing(id=i) for i in range(1, 41)], "metadata": {"total_elements": 120}}
    second = {"data": [listing(id=i) for i in range(41, 61)], "metadata": {"total_elements": 120}}
    stub = StubOlxClient(pages=[first, second])
    products = await OlxSource(client=stub).search("iphone")
    assert len(products) == 60
    assert [s["offset"] for s in stub.searches] == [0, 40]


async def test_no_second_page_when_the_first_already_holds_everything():
    page = {"data": [listing(id=i) for i in range(1, 41)], "metadata": {"total_elements": 40}}
    stub = StubOlxClient(pages=[page])
    await OlxSource(client=stub).search("iphone")
    assert len(stub.searches) == 1


async def test_no_second_page_when_the_target_is_already_met():
    page = {"data": [listing(id=i) for i in range(1, 61)], "metadata": {"total_elements": 500}}
    stub = StubOlxClient(pages=[page])
    await OlxSource(client=stub).search("iphone")
    assert len(stub.searches) == 1


def test_a_different_city_builds_a_separate_client():
    source = OlxSource(client=StubOlxClient())
    other = source._client_for_city("2")
    assert isinstance(other, OlxClient)
    assert other.city_id == "2"
