"""Regression tests for the logic-map findings fixed on 2026-11-05 (ids in the plan)."""

from __future__ import annotations

import logging

import httpx
import pytest
from fakes import FakePage, PageFactory
from test_kimovil import kimovil_source
from test_olx import StubOlxClient, listing
from test_ozon import StubBrowser, page_with, search_tile

import market_sources.ozon.browser as ozon_browser
from market_sources import impersonate
from market_sources.kaspi.client import KaspiClient
from market_sources.kaspi.source import KaspiSource
from market_sources.kimovil.client import KimovilClient
from market_sources.kimovil.parse import parse_autocomplete
from market_sources.olx.client import OlxClient
from market_sources.olx.parse import parse_specs
from market_sources.olx.source import OlxSource
from market_sources.ozon.browser import OzonBrowser, OzonError
from market_sources.ozon.parse import parse_offer, parse_search
from market_sources.ozon.source import OzonSource


def olx_page(first: int, count: int, total: int) -> dict:
    ids = range(first, first + count)
    return {"data": [listing(id=i) for i in ids], "metadata": {"total_elements": total}}


# ---- #1 paging steps by the page size OLX really returns


async def test_search_cheapest_steps_by_the_page_size_olx_returns():
    pages = [olx_page(1, 40, 500), olx_page(41, 40, 500), olx_page(81, 40, 500)]
    stub = StubOlxClient(pages=pages)
    await OlxSource(client=stub).search_cheapest("x", limit=100)
    assert [s["offset"] for s in stub.searches] == [0, 40, 80]


async def test_search_all_steps_by_the_page_size_olx_returns():
    stub = StubOlxClient(pages=[olx_page(1, 40, 120)])
    await OlxSource(client=stub).search_all("x", 0, 10)
    assert sorted(s["offset"] for s in stub.searches) == [0, 40, 80]


# ---- #6 search_all warns on a capped slice and survives a failed page


async def test_search_all_warns_when_a_slice_is_still_capped(caplog):
    stub = StubOlxClient(pages=[olx_page(1, 50, 1000)])
    with caplog.at_level(logging.WARNING):
        await OlxSource(client=stub).search_all("x", 0, 500)  # width < min slice: not split
    assert any("capped" in r.message for r in caplog.records)


async def test_search_all_keeps_the_pages_it_got_when_one_fails(caplog):
    class Flaky(StubOlxClient):
        async def search(self, query, offset=0, **kwargs):
            if offset == 50:
                raise httpx.ReadTimeout("boom")
            return olx_page(offset + 1, 50, 150)

    with caplog.at_level(logging.WARNING):
        products = await OlxSource(client=Flaky()).search_all("x", 0, 10)
    assert len(products) == 100  # offsets 0 and 100
    assert any("offset 50" in r.message for r in caplog.records)


async def test_search_all_logs_failure_type_source_and_a_summary(caplog):
    class Flaky(StubOlxClient):
        async def search(self, query, offset=0, **kwargs):
            if offset == 50:
                raise httpx.ReadTimeout("boom")
            return olx_page(offset + 1, 50, 150)

    with caplog.at_level(logging.WARNING):
        await OlxSource(client=Flaky()).search_all("x", 0, 10)
    msgs = [r.getMessage() for r in caplog.records]
    assert any("OLX" in m and "ReadTimeout" in m and "boom" in m and "'x'" in m for m in msgs)
    assert any("1 of 2 pages failed" in m for m in msgs)


# ---- #9 int id hits the cache


async def test_an_int_product_id_hits_the_offer_cache():
    stub = StubOlxClient()
    source = OlxSource(client=stub)
    source._cache_offers({"data": [listing(id=111)]})
    [offer] = await source.offers(111)  # type: ignore[arg-type]
    assert offer.product_id == "111" and stub.details == []


# ---- #11 unknown seller type is omitted


def test_seller_type_is_omitted_when_the_listing_does_not_say():
    assert "seller_type" not in parse_specs(listing())
    assert parse_specs({}) == {}


# ---- #8 Kimovil non-JSON 200


async def test_a_non_json_autocomplete_is_a_miss_not_a_crash():
    source, _ = kimovil_source([httpx.Response(200, text="<html>cf</html>")] * 5)
    assert await source.lookup("Apple iPhone 13") is None


def test_a_non_object_autocomplete_payload_has_no_hits():
    assert parse_autocomplete([]) == []  # type: ignore[arg-type]


# ---- #4 warn when curl_cffi is missing


def test_olx_and_kimovil_warn_without_curl_cffi(monkeypatch, caplog):
    monkeypatch.setattr(impersonate, "available", lambda: False)
    with caplog.at_level(logging.WARNING):
        OlxClient()
        KimovilClient()
    assert len([r for r in caplog.records if "curl_cffi" in r.message]) == 2


# ---- #7 extra city clients keep the proxy of a client with no .http


def test_extra_city_clients_use_the_source_proxy_when_the_client_has_no_http(monkeypatch):
    from test_kaspi import StubKaspiClient

    monkeypatch.delenv("KASPI_PROXY", raising=False)
    olx = OlxSource(client=StubOlxClient(), proxy="http://p.test:1")
    assert olx._client_for_city("2").http.proxy == "http://p.test:1"
    kaspi = KaspiSource(client=StubKaspiClient(), proxy="http://p.test:2")
    extra = kaspi._client_for_city("710000000")
    assert isinstance(extra, KaspiClient) and extra.http.proxy == "http://p.test:2"


# ---- #2 first Ozon bootstrap


async def test_ozon_bootstrap_without_a_state_file_saves_to_the_default_path(monkeypatch, tmp_path):
    target = tmp_path / "ozon_state.json"
    monkeypatch.setenv("OZON_STATE_PATH", str(target))
    ozon = OzonBrowser(page_factory=PageFactory(FakePage()))
    assert ozon.state_path is None
    seen = {}

    async def fake_bootstrap(self, probe_url, **kwargs):
        seen["state"], seen["probe"] = self.state_path, probe_url
        return True

    monkeypatch.setattr(ozon_browser.BrowserSession, "bootstrap", fake_bootstrap)
    assert await ozon.bootstrap() is True
    assert seen["state"] == target
    assert "composer-api" in seen["probe"]


# ---- #3 Ozon product page fetched once for offers + specs


async def test_offers_and_specs_share_one_product_page_fetch():
    stub = StubBrowser(page_with(webPrice={"price": "89 990 ₸"}))
    source = OzonSource(browser=stub)
    await source.offers("1")
    await source.specs("1")
    assert stub.paths == ["/product/1/"]
    await source.offers("2")
    assert stub.paths == ["/product/1/", "/product/2/"]


async def test_a_cached_product_page_expires(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("market_sources.ozon.source.time.monotonic", lambda: now[0])
    stub = StubBrowser(page_with(webPrice={"price": "1 ₸"}))
    source = OzonSource(browser=stub)
    await source.offers("1")
    now[0] += 3600
    await source.offers("1")
    assert stub.paths == ["/product/1/", "/product/1/"]


# ---- #12 Ozon failures stay inside OzonError


async def test_a_navigation_timeout_is_an_ozon_error():
    class TimeoutError(Exception):  # noqa: A001 - mimics playwright's class name
        pass

    ozon = OzonBrowser(
        page_factory=PageFactory(FakePage(script=[200, TimeoutError("Timeout 90000ms")])),
        idle_timeout_s=0,
    )
    with pytest.raises(OzonError, match="timed out"):
        await ozon.fetch_json("/search/?text=x")


def test_a_non_numeric_star_rating_does_not_lose_the_page():
    tile = search_tile()
    tile["mainState"][-1]["labelListV2"]["items"][0]["text"]["text"] = "нет"
    parse_search(page_with(tileGridDesktop={"items": [tile]}))  # must not raise


def test_a_non_numeric_seller_rating_is_none():
    page = page_with(
        webPrice={"price": "1 ₸"},
        webCurrentSeller={
            "sellerCell": {"centerBlock": {"title": {"text": "Shop"}}},
            "rating": {"title": {"text": "новый"}},
        },
    )
    assert parse_offer(page, "1") is not None
