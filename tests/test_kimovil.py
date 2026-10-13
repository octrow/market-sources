"""Kimovil: pure parsers over minimal captured-shape payloads, plus source wiring."""

from __future__ import annotations

import httpx
import pytest
from fakes import LowRng, RecordingSleep, ScriptedTransport

from market_sources.kimovil.client import BASE_URL, KimovilClient
from market_sources.kimovil.parse import (
    KimovilHit,
    best_match,
    model_query,
    parse_autocomplete,
    parse_phone,
)
from market_sources.kimovil.source import KimovilSource
from market_sources.polite_http import PoliteClient

PAGE = """<html><body>
<h1>Price and specifications on
  Samsung Galaxy S23 Ultra</h1>
<table>
<tr><td>Diagonal</td><td>6.8" See more details</td></tr>
<tr><td>Type</td><td>Super AMOLED
\t\t\tDynamic AMOLED 2X</td></tr>
<tr><td>Resolution</td><td>1440 x 3088 px</td></tr>
<tr><td>Others</td><td>Hole-punch Notch Refresh rate 120 Hz</td></tr>
<tr><td>Model</td><td>Qualcomm Snapdragon 8 Gen2</td></tr>
<tr><td>Score</td><td>1.521.862 • Antutu v10</td></tr>
<tr><td>Capacity</td><td>256 GB</td></tr>
<tr><td>Resolution</td><td>200 Mpx</td></tr>
<tr><td>Type</td><td>ISOCELL</td></tr>
<tr><td>Capacity</td><td>5000 mAhSee more details</td></tr>
<tr><td>Type</td><td>Li-Polymer</td></tr>
<tr><td>Fast charge</td><td>Yes , 45.0W</td></tr>
<tr><td>Pros</td><td>Pros</td></tr>
<tr><td>only one cell</td></tr>
</table>
<dl class="k-dl dxomark-scores-dl"><dt>DxOMark Score</dt><dd>140Camera-Photo-Video</dd></dl>
</body></html>"""


def hit(full_name: str, slug: str, **extra) -> dict:
    return {
        "full_name": full_name,
        "url": slug,
        "ki": 80.0,
        "usd": 300.0,
        "is_rumor": False,
        "result_type": "smartphones",
        **extra,
    }


# ------------------------------------------------------------------ model_query


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Смартфон Apple iPhone 13 128Gb черный", "apple iphone 13"),
        ("Samsung Galaxy A55 5G 8 ГБ/256 ГБ голубой", "samsung galaxy a55 5g"),
        ("Смартфон Xiaomi Redmi Note 13 Pro 8/256Gb (2024)", "xiaomi redmi note 13 pro"),
        (
            "Смартфон Samsung Galaxy S23 Ultra 12/512GB, Phantom Black",
            "samsung galaxy s23 ultra phantom black",
        ),
    ],
)
def test_model_query_keeps_brand_and_model_and_drops_memory_and_russian(title, expected):
    assert model_query(title) == expected


# ------------------------------------------------------------------ best_match


def test_the_exact_model_wins_over_a_longer_sibling():
    hits = parse_autocomplete(
        {"results": [hit("Apple iPhone 13 Pro Max", "a"), hit("Apple iPhone 13", "b")]}
    )
    assert best_match("apple iphone 13", hits).slug == "b"


def test_the_most_specific_contained_model_wins():
    hits = parse_autocomplete(
        {"results": [hit("Apple iPhone 13", "a"), hit("Apple iPhone 13 Pro", "b")]}
    )
    assert best_match("apple iphone 13 pro", hits).slug == "b"


def test_a_network_suffix_is_optional_and_extra_query_words_are_tolerated():
    hits = parse_autocomplete(
        {"results": [hit("POCO X6 Neo", "neo"), hit("POCO X6 Pro 5G", "pro")]}
    )
    assert best_match("xiaomi poco x6 pro phantom", hits).slug == "pro"


def test_no_match_when_every_hit_names_something_the_query_does_not():
    hits = parse_autocomplete({"results": [hit("POCO X6 Neo", "neo")]})
    assert best_match("xiaomi redmi note 13 pro", hits) is None


def test_rumours_and_non_phones_are_skipped():
    payload = {
        "results": [
            hit("Apple iPhone 13", "rumour", is_rumor=True),
            hit("Apple iPhone 13", "tablet", result_type="tablets"),
        ]
    }
    assert parse_autocomplete(payload) == []


def test_autocomplete_tolerates_missing_fields():
    [only] = parse_autocomplete({"results": [{"full_name": "Apple iPhone 13", "url": "x"}, {}]})
    assert only == KimovilHit(full_name="Apple iPhone 13", slug="x", ki=None, usd=None)


# ------------------------------------------------------------------ parse_phone


def test_parse_phone_reads_rows_first_wins_and_splits_battery_from_storage():
    hit_ = KimovilHit(full_name="Samsung Galaxy S23 Ultra", slug="s23u", ki=84.0, usd=726.0)
    phone = parse_phone(PAGE, hit_)
    assert phone.full_name == "Samsung Galaxy S23 Ultra"
    assert phone.url == f"{BASE_URL}/en/where-to-buy-s23u"
    assert phone.ki == 84.0
    assert phone.specs["Type"] == "Super AMOLED Dynamic AMOLED 2X"  # whitespace collapsed
    assert phone.specs["Resolution"] == "1440 x 3088 px"  # screen, not the camera row
    assert phone.specs["Capacity"] == "256 GB"
    assert phone.specs["Battery capacity"] == "5000 mAhSee more details"
    assert phone.specs["DxOMark"] == "140"
    assert "Pros" not in phone.specs  # label == value rows carry nothing


def test_parse_phone_without_dxomark_has_no_such_key():
    phone = parse_phone(
        "<table><tr><td>Model</td><td>X</td></tr></table>", KimovilHit(full_name="A", slug="a")
    )
    assert phone.specs == {"Model": "X"}


# ------------------------------------------------------------------ source wiring


def kimovil_source(script) -> tuple[KimovilSource, ScriptedTransport]:
    transport = ScriptedTransport(script)
    http = PoliteClient(
        user_agent="UA",
        transport=transport,
        sleep=RecordingSleep(),
        rng=LowRng(),
        min_delay_s=0,
        max_delay_s=0,
    )
    return KimovilSource(client=KimovilClient(http=http)), transport


async def test_lookup_resolves_the_title_then_parses_the_page():
    source, transport = kimovil_source(
        [
            httpx.Response(200, json={"results": [hit("Samsung Galaxy S23 Ultra", "s23u")]}),
            httpx.Response(200, text=PAGE),
        ]
    )
    phone = await source.lookup("Смартфон Samsung Galaxy S23 Ultra 12/256GB")
    assert phone is not None and phone.specs["Battery capacity"].startswith("5000")
    search, page = transport.requests
    assert dict(search.url.params)["name"] == "samsung galaxy s23 ultra"
    assert str(page.url) == f"{BASE_URL}/en/where-to-buy-s23u"


async def test_lookup_retries_without_the_brand_word():
    source, transport = kimovil_source(
        [
            httpx.Response(200, json={"results": []}),
            httpx.Response(200, json={"results": [hit("POCO X6 Pro 5G", "x6p")]}),
            httpx.Response(200, text=PAGE),
        ]
    )
    phone = await source.lookup("Xiaomi Poco X6 Pro 12/512Gb")
    assert phone is not None and phone.slug == "x6p"
    assert dict(transport.requests[1].url.params)["name"] == "poco x6 pro"


async def test_lookup_returns_none_when_nothing_matches():
    source, transport = kimovil_source([httpx.Response(200, json={"results": []})])
    assert await source.lookup("Смартфон Nokia 3310") is None
    assert len(transport.requests) == 2  # full query, then without the brand word


async def test_a_page_error_is_a_miss_not_a_crash():
    source, _ = kimovil_source(
        [
            httpx.Response(200, json={"results": [hit("Apple iPhone 13", "i13")]}),
            httpx.Response(404),
        ]
    )
    assert await source.lookup("Apple iPhone 13") is None


def test_a_plus_model_is_a_different_model():
    hits = parse_autocomplete({"results": [hit("Tecno Spark 20 Pro+", "plus")]})
    assert best_match("tecno spark 20 pro", hits) is None


async def test_lookup_trims_trailing_words_kimovil_cannot_find():
    def search(request: httpx.Request) -> httpx.Response:
        name = request.url.params["name"]
        found = (
            [hit("Samsung Galaxy S24 Ultra", "s24u")] if name == "samsung galaxy s24 ultra" else []
        )
        return httpx.Response(200, json={"results": found})

    source, transport = kimovil_source(
        [search, search, search, search, httpx.Response(200, text=PAGE)]
    )
    phone = await source.lookup("Смартфон Samsung Galaxy S24 Ultra 12/256GB Titanium Gray")
    assert phone is not None and phone.slug == "s24u"
    names = [r.url.params.get("name") for r in transport.requests[:-1]]
    assert names == [
        "samsung galaxy s24 ultra titanium gray",
        "galaxy s24 ultra titanium gray",
        "samsung galaxy s24 ultra titanium",
        "samsung galaxy s24 ultra",
    ]


def test_the_parent_brand_may_be_missing_from_the_title():
    hits = parse_autocomplete({"results": [hit("Xiaomi Redmi Note 12 Pro 5G", "rn12p")]})
    assert best_match("redmi note 12 pro 5g", hits).slug == "rn12p"


def test_a_name_with_its_brand_present_outranks_one_missing_it():
    hits = parse_autocomplete(
        {"results": [hit("Xiaomi Redmi Note 12 Pro", "a"), hit("Redmi Note 12 Pro", "b")]}
    )
    assert best_match("redmi note 12 pro", hits).slug == "b"
