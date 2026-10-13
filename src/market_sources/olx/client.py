"""Async client for the OLX.kz public JSON API.

OLX.kz exposes the same REST API its own frontend uses, behind a CloudFront WAF
that fingerprints the TLS handshake: httpx gets 403 even with browser headers
(2026-10-13), so requests go through :class:`ImpersonateTransport` (curl_cffi)
when the ``impersonate`` extra is installed. No referer requirement. Gentle by default (semaphore of 2 + 0.5–1.5 s jittered delay) to
stay a good citizen; datacenter IPs are untested and may need a proxy later.

All pacing/headers/retry live in :class:`market_sources.polite_http.PoliteClient`;
this module is only the endpoint shapes.
"""

from __future__ import annotations

from typing import Any

from market_sources import impersonate
from market_sources.impersonate import ImpersonateTransport
from market_sources.polite_http import PoliteClient

BASE_URL = "https://www.olx.kz"
DEFAULT_CITY_ID = "1"  # город Алматы. region_id 8 is the broader oblast.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)

MAX_CONCURRENCY = 2
MIN_DELAY_S = 0.5
MAX_DELAY_S = 1.5
TIMEOUT_S = 15.0
MAX_RETRIES = 1
DEFAULT_LIMIT = 50  # OLX caps a single page lower (~40); OlxSource paginates once


class OlxClient:
    """Thin async wrapper around the OLX.kz public offers API."""

    def __init__(
        self,
        city_id: str = DEFAULT_CITY_ID,
        *,
        proxy: str | None = None,
        http: PoliteClient | None = None,
    ) -> None:
        self.city_id = city_id
        self.http = http or PoliteClient(
            user_agent=USER_AGENT,
            max_concurrency=MAX_CONCURRENCY,
            min_delay_s=MIN_DELAY_S,
            max_delay_s=MAX_DELAY_S,
            timeout_s=TIMEOUT_S,
            max_retries=MAX_RETRIES,
            proxy=proxy,
            transport=ImpersonateTransport(proxy=proxy) if impersonate.available() else None,
        )

    async def search(
        self,
        query: str,
        offset: int = 0,
        limit: int = DEFAULT_LIMIT,
        budget: int | None = None,
        min_price: int | None = None,
    ) -> Any:
        """Call ``GET /api/v1/offers/`` (query, city filter) and return JSON.

        ``budget`` / ``min_price`` become ``filter_float_price:to`` / ``:from``:
        OLX filters server-side, both bounds inclusive.
        """
        params: dict[str, object] = {
            "offset": offset,
            "limit": limit,
            "query": query,
            "city_id": self.city_id,
        }
        if min_price is not None:
            params["filter_float_price:from"] = min_price
        if budget is not None:
            params["filter_float_price:to"] = budget
        return await self.http.get_json(f"{BASE_URL}/api/v1/offers/", params=params)

    async def detail(self, offer_id: str) -> Any:
        """Call ``GET /api/v1/offers/{id}/`` and return decoded JSON."""
        return await self.http.get_json(f"{BASE_URL}/api/v1/offers/{offer_id}/")

    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self.http.aclose()


__all__ = ["BASE_URL", "DEFAULT_CITY_ID", "DEFAULT_LIMIT", "USER_AGENT", "OlxClient"]
