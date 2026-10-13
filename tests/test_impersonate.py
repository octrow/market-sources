"""``ImpersonateTransport``: curl_cffi under httpx, so PoliteClient pacing/retry stay."""

from __future__ import annotations

import httpx
import pytest

from market_sources import impersonate
from market_sources.impersonate import ImpersonateTransport
from market_sources.olx.client import OlxClient


class _Resp:
    def __init__(
        self, status: int, headers: list[tuple[str, str]], content: bytes
    ) -> None:
        self.status_code = status
        self.content = content
        self._headers = headers

    @property
    def headers(self):
        outer = self

        class _H:
            def multi_items(self):
                return outer._headers

        return _H()


class _FakeSession:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self._response = response
        self._error = error

    async def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if self._error:
            raise self._error
        return self._response

    async def close(self) -> None:
        pass


async def test_maps_response_and_drops_encoding_headers() -> None:
    session = _FakeSession(
        _Resp(
            200,
            [("content-encoding", "br"), ("content-length", "3"), ("x-a", "1")],
            b'{"a"',
        )
    )
    async with httpx.AsyncClient(
        transport=ImpersonateTransport(session=session)
    ) as client:
        resp = await client.get(
            "https://olx.test/api?q=1", headers={"user-agent": "UA"}
        )

    assert resp.status_code == 200 and resp.content == b'{"a"'
    assert "content-encoding" not in resp.headers and resp.headers["x-a"] == "1"
    call = session.calls[0]
    assert (call["method"], call["url"]) == ("GET", "https://olx.test/api?q=1")
    assert call["headers"]["user-agent"] == "UA"
    assert (
        "accept-encoding" not in call["headers"]
    )  # curl_cffi negotiates + decodes itself
    assert call["allow_redirects"] is False  # httpx owns redirects


async def test_curl_error_becomes_httpx_transport_error(monkeypatch) -> None:
    class _CurlBoom(OSError):
        pass

    monkeypatch.setattr(impersonate, "_curl_errors", lambda: (_CurlBoom,))
    session = _FakeSession(error=_CurlBoom("reset"))
    async with httpx.AsyncClient(
        transport=ImpersonateTransport(session=session)
    ) as client:
        with pytest.raises(httpx.TransportError):
            await client.get("https://olx.test/")


def test_olx_client_uses_impersonation_when_available(monkeypatch) -> None:
    monkeypatch.setattr(impersonate, "available", lambda: True)
    assert isinstance(
        OlxClient(proxy="http://p:1").http._transport, ImpersonateTransport
    )
    assert OlxClient(proxy="http://p:1").http._transport._proxy == "http://p:1"

    monkeypatch.setattr(impersonate, "available", lambda: False)
    assert OlxClient().http._transport is None
