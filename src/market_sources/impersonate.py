"""``ImpersonateTransport`` — an httpx transport that sends through curl_cffi.

Some sites sit behind a WAF that fingerprints the TLS/HTTP2 handshake: OLX.kz's
CloudFront answers 403 to httpx and plain curl even with a browser UA, while a
real Chrome (or curl_cffi impersonating one) gets 200 — live-checked 2026-10-13,
~0.5 s per request versus ~18 s to launch Playwright.

Plugging in at the transport level keeps :class:`~market_sources.polite_http.PoliteClient`
unchanged: pacing, retries, redirects and ``raise_for_status`` stay httpx's.
``curl_cffi`` is the optional ``impersonate`` extra and is imported lazily, so this
module imports fine without it; check :func:`available` before choosing it.
"""

from __future__ import annotations

import importlib.util
from typing import Any

import httpx

DEFAULT_IMPERSONATE = "chrome"

# curl_cffi negotiates and decodes compression itself; forwarding these would
# make libcurl return raw bytes or make httpx decode twice.
_DROP_REQUEST_HEADERS = frozenset(
    {"accept-encoding", "host", "connection", "content-length"}
)
_DROP_RESPONSE_HEADERS = frozenset(
    {"content-encoding", "content-length", "transfer-encoding"}
)


def available() -> bool:
    """True when the ``impersonate`` extra (curl_cffi) is installed."""
    return importlib.util.find_spec("curl_cffi") is not None


def _curl_errors() -> tuple[type[BaseException], ...]:
    from curl_cffi.requests.exceptions import RequestException

    return (RequestException,)


class ImpersonateTransport(httpx.AsyncBaseTransport):
    """Send httpx requests through a curl_cffi session impersonating a browser."""

    def __init__(
        self,
        impersonate: str = DEFAULT_IMPERSONATE,
        *,
        proxy: str | None = None,
        session: Any | None = None,
    ) -> None:
        self._impersonate = impersonate
        self._proxy = proxy or None
        self._session = session

    def _ensure_session(self) -> Any:
        if self._session is None:
            from curl_cffi.requests import AsyncSession

            self._session = AsyncSession(
                impersonate=self._impersonate, proxy=self._proxy
            )
        return self._session

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        headers = {
            k: v for k, v in request.headers.items() if k not in _DROP_REQUEST_HEADERS
        }
        body = await request.aread()
        try:
            resp = await self._ensure_session().request(
                request.method,
                str(request.url),
                headers=headers,
                content=body or None,
                timeout=request.extensions.get("timeout", {}).get("read"),
                allow_redirects=False,
            )
        except _curl_errors() as exc:
            raise httpx.TransportError(f"curl_cffi: {exc}", request=request) from exc
        return _to_httpx(resp, request)

    async def aclose(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None


def _to_httpx(resp: Any, request: httpx.Request) -> httpx.Response:
    """Rebuild a curl_cffi response as httpx's, minus already-decoded encodings."""
    headers = [
        (k, v)
        for k, v in resp.headers.multi_items()
        if k.lower() not in _DROP_RESPONSE_HEADERS
    ]
    return httpx.Response(
        resp.status_code, headers=headers, content=resp.content, request=request
    )


__all__ = ["DEFAULT_IMPERSONATE", "ImpersonateTransport", "available"]
