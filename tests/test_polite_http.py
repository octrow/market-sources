"""PoliteClient: header wiring, pacing, and the retry/backoff schedule."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fakes import HighRng, LowRng, RecordingSleep, ScriptedTransport

from market_sources import polite_http
from market_sources.polite_http import (
    DEFAULT_ACCEPT_LANGUAGE,
    DEFAULT_USER_AGENT,
    PoliteClient,
    parse_retry_after,
)

URL = "https://example.test/api"


def make_client(script=None, **kwargs) -> tuple[PoliteClient, ScriptedTransport, RecordingSleep]:
    transport = ScriptedTransport(script)
    sleep = RecordingSleep()
    kwargs.setdefault("min_delay_s", 0.5)
    kwargs.setdefault("max_delay_s", 1.5)
    client = PoliteClient(
        transport=transport, sleep=sleep, rng=LowRng(), **kwargs
    )
    return client, transport, sleep


# --------------------------------------------------------------------- headers


async def test_browser_headers_are_sent_by_default():
    client, transport, _ = make_client()
    await client.get(URL)
    sent = transport.requests[0].headers
    assert sent["user-agent"] == DEFAULT_USER_AGENT
    assert sent["accept-language"] == DEFAULT_ACCEPT_LANGUAGE
    assert sent["accept"] == "application/json, text/plain, */*"


async def test_constructor_headers_and_user_agent_override_defaults():
    client, transport, _ = make_client(
        user_agent="MyUA/1.0", headers={"Accept": "application/json, text/*", "X-Ks": "1"}
    )
    await client.get(URL)
    sent = transport.requests[0].headers
    assert sent["user-agent"] == "MyUA/1.0"
    assert sent["accept"] == "application/json, text/*"
    assert sent["x-ks"] == "1"


async def test_per_request_headers_and_referer_win():
    client, transport, _ = make_client(headers={"x-ks-city": "750000000"})
    await client.get(URL, referer="https://kaspi.kz/shop/search/", headers={"x-ks-city": "710000000"})
    sent = transport.requests[0].headers
    assert sent["referer"] == "https://kaspi.kz/shop/search/"
    assert sent["x-ks-city"] == "710000000"


def test_merge_headers_is_case_insensitive_and_does_not_mutate_base():
    client = PoliteClient(headers={"X-A": "1"}, transport=ScriptedTransport())
    merged = client.merge_headers({"X-A": "2"}, referer="r")
    assert merged["x-a"] == "2"
    assert merged["referer"] == "r"
    assert client.base_headers["x-a"] == "1"


# ---------------------------------------------------------------------- pacing


async def test_a_jittered_delay_precedes_every_request():
    client, _, sleep = make_client(min_delay_s=0.5, max_delay_s=1.5)
    await client.get(URL)
    await client.get(URL)
    assert sleep.calls == [0.5, 0.5]  # LowRng -> the low end of the range


async def test_high_jitter_uses_the_top_of_the_range():
    transport = ScriptedTransport()
    sleep = RecordingSleep()
    client = PoliteClient(transport=transport, sleep=sleep, rng=HighRng(), max_delay_s=1.5)
    await client.get(URL)
    assert sleep.calls == [1.5]


async def test_zero_delay_skips_sleeping_entirely():
    client, _, sleep = make_client(min_delay_s=0, max_delay_s=0)
    await client.get(URL)
    assert sleep.calls == []


async def test_semaphore_caps_concurrency():
    client, transport, _ = make_client(max_concurrency=2, min_delay_s=0, max_delay_s=0)
    await asyncio.gather(*(client.get(URL) for _ in range(6)))
    assert len(transport.requests) == 6
    assert transport.max_inflight == 2


async def test_max_concurrency_one_serializes():
    client, transport, _ = make_client(max_concurrency=1, min_delay_s=0, max_delay_s=0)
    await asyncio.gather(*(client.get(URL) for _ in range(4)))
    assert transport.max_inflight == 1


# ----------------------------------------------------------------------- retry


async def test_retries_a_500_then_succeeds():
    client, transport, sleep = make_client(
        [httpx.Response(500), httpx.Response(200, json={"ok": True})],
        min_delay_s=0,
        max_delay_s=0,
    )
    assert await client.get_json(URL) == {"ok": True}
    assert len(transport.requests) == 2
    assert sleep.calls == [1.0]  # backoff_base * 2**0, LowRng adds no jitter


async def test_backoff_is_exponential_and_capped():
    client, transport, sleep = make_client(
        [httpx.Response(503)] * 5 + [httpx.Response(200, json={})],
        min_delay_s=0,
        max_delay_s=0,
        max_retries=5,
        backoff_base_s=1.0,
        backoff_max_s=4.0,
    )
    await client.get(URL)
    assert sleep.calls == [1.0, 2.0, 4.0, 4.0, 4.0]
    assert len(transport.requests) == 6


async def test_jitter_is_added_on_top_of_the_exponential_base():
    transport = ScriptedTransport([httpx.Response(500), httpx.Response(200, json={})])
    sleep = RecordingSleep()
    client = PoliteClient(
        transport=transport,
        sleep=sleep,
        rng=HighRng(),
        min_delay_s=0,
        max_delay_s=0,
        backoff_jitter=0.25,
    )
    await client.get(URL)
    assert sleep.calls == [1.25]  # 1.0 base + full 25% jitter


async def test_giving_up_after_max_retries_raises_the_status():
    client, transport, _ = make_client(
        [httpx.Response(503)], min_delay_s=0, max_delay_s=0, max_retries=2
    )
    with pytest.raises(httpx.HTTPStatusError) as err:
        await client.get(URL)
    assert err.value.response.status_code == 503
    assert len(transport.requests) == 3


async def test_a_404_is_an_answer_not_a_hiccup():
    client, transport, sleep = make_client(
        [httpx.Response(404)], min_delay_s=0, max_delay_s=0, max_retries=3
    )
    with pytest.raises(httpx.HTTPStatusError):
        await client.get(URL)
    assert len(transport.requests) == 1
    assert sleep.calls == []


async def test_429_is_retried_even_though_it_is_a_4xx():
    client, transport, _ = make_client(
        [httpx.Response(429), httpx.Response(200, json={})], min_delay_s=0, max_delay_s=0
    )
    await client.get(URL)
    assert len(transport.requests) == 2


async def test_retry_after_seconds_is_honored_over_backoff():
    client, _, sleep = make_client(
        [
            httpx.Response(429, headers={"retry-after": "7"}),
            httpx.Response(200, json={}),
        ],
        min_delay_s=0,
        max_delay_s=0,
    )
    await client.get(URL)
    assert sleep.calls == [7.0]


async def test_retry_after_is_clamped_to_backoff_max():
    client, _, sleep = make_client(
        [httpx.Response(503, headers={"retry-after": "600"}), httpx.Response(200, json={})],
        min_delay_s=0,
        max_delay_s=0,
        backoff_max_s=30.0,
    )
    await client.get(URL)
    assert sleep.calls == [30.0]


async def test_retry_after_http_date_is_honored():
    when = datetime.now(UTC) + timedelta(seconds=5)
    client, _, sleep = make_client(
        [
            httpx.Response(503, headers={"retry-after": when.strftime("%a, %d %b %Y %H:%M:%S GMT")}),
            httpx.Response(200, json={}),
        ],
        min_delay_s=0,
        max_delay_s=0,
    )
    await client.get(URL)
    assert 0 <= sleep.calls[0] <= 6


async def test_transport_errors_are_retried_then_reraised():
    client, transport, sleep = make_client(
        [httpx.ConnectError("boom")], min_delay_s=0, max_delay_s=0, max_retries=2
    )
    with pytest.raises(httpx.ConnectError):
        await client.get(URL)
    assert len(transport.requests) == 3
    assert sleep.calls == [1.0, 2.0]


async def test_a_transport_error_that_recovers():
    client, _, _ = make_client(
        [httpx.ReadTimeout("slow"), httpx.Response(200, json={"ok": 1})],
        min_delay_s=0,
        max_delay_s=0,
    )
    assert await client.get_json(URL) == {"ok": 1}


# ----------------------------------------------------- idempotency of retries


async def test_post_is_not_retried_by_default():
    client, transport, _ = make_client(
        [httpx.Response(500)], min_delay_s=0, max_delay_s=0, max_retries=3
    )
    with pytest.raises(httpx.HTTPStatusError):
        await client.post(URL, json={})
    assert len(transport.requests) == 1


async def test_post_is_retried_when_the_caller_opts_in():
    client, transport, _ = make_client(
        [httpx.Response(500), httpx.Response(200, json={})], min_delay_s=0, max_delay_s=0
    )
    await client.post_json(URL, json={}, retry=True)
    assert len(transport.requests) == 2


async def test_get_can_opt_out_of_retries():
    client, transport, _ = make_client(
        [httpx.Response(500)], min_delay_s=0, max_delay_s=0, max_retries=3
    )
    with pytest.raises(httpx.HTTPStatusError):
        await client.get(URL, retry=False)
    assert len(transport.requests) == 1


# ------------------------------------------------------------------- plumbing


async def test_proxy_is_passed_to_the_underlying_client(monkeypatch):
    captured: dict = {}
    real = httpx.AsyncClient

    class Spy(real):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            super().__init__(**kwargs)

    monkeypatch.setattr(polite_http.httpx, "AsyncClient", Spy)
    client = PoliteClient(proxy="http://user:pw@proxy.test:8080", timeout_s=12.5)
    client._ensure_client()
    assert captured["proxy"] == "http://user:pw@proxy.test:8080"
    assert captured["timeout"] == 12.5
    assert captured["follow_redirects"] is True
    await client.aclose()


def test_empty_proxy_string_means_no_proxy():
    assert PoliteClient(proxy="").proxy is None


async def test_the_http_client_is_reused_across_requests():
    client, _, _ = make_client(min_delay_s=0, max_delay_s=0)
    await client.get(URL)
    first = client._client
    await client.get(URL)
    assert client._client is first
    await client.aclose()
    assert client._client is None


async def test_usable_as_an_async_context_manager():
    transport = ScriptedTransport()
    async with PoliteClient(transport=transport, min_delay_s=0, max_delay_s=0) as client:
        await client.get(URL)
    assert client._client is None


def test_bad_construction_is_rejected():
    with pytest.raises(ValueError):
        PoliteClient(min_delay_s=2.0, max_delay_s=1.0)
    with pytest.raises(ValueError):
        PoliteClient(max_retries=-1)


# ------------------------------------------------------- parse_retry_after unit


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("3", 3.0),
        (" 2.5 ", 2.5),
        ("-5", 0.0),
        ("not-a-date", None),
    ],
)
def test_parse_retry_after_scalars(value, expected):
    assert parse_retry_after(value) == expected


def test_parse_retry_after_http_date_in_the_past_is_zero():
    past = datetime(2000, 1, 1, tzinfo=UTC)
    assert parse_retry_after(past.strftime("%a, %d %b %Y %H:%M:%S GMT")) == 0.0


def test_parse_retry_after_http_date_uses_the_injected_now():
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    header = "Thu, 01 Jan 2026 12:00:30 GMT"
    assert parse_retry_after(header, now=now) == 30.0
