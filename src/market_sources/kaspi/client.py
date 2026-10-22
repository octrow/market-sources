"""Async client for the 4 Kaspi.kz internal JSON endpoints.

Anti-bot gate is **referer-based**: a normal browser UA plus a ``referer`` that
matches the page the call would have come from is enough for 200s. Gentle by
default (semaphore of 2 + 0.5–1.5 s jittered delay) since a residential IP was
not throttled, but datacenter IPs may still need the optional proxy — hence
``KASPI_PROXY``.

All pacing/headers/retry live in :class:`market_sources.polite_http.PoliteClient`;
this module is only the endpoint shapes.
"""

from __future__ import annotations

import os
from typing import Any

from market_sources.polite_http import PoliteClient

BASE_URL = "https://kaspi.kz"
DEFAULT_CITY = "750000000"  # Almaty. 710000000 is Astana — do not swap.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)
PROXY_ENV_VAR = "KASPI_PROXY"

MAX_CONCURRENCY = 2
MIN_DELAY_S = 0.5
MAX_DELAY_S = 1.5
TIMEOUT_S = 10.0
MAX_RETRIES = 1

# Kaspi filters price only by these fixed buckets (lower bound, label), the same
# for every category checked live. A budget selects each bucket starting at or
# below it, so the ceiling is the top of the budget's bucket, not the budget.
# ponytail: hardcoded labels; read them from the response's price filter if
# Kaspi ever renames them.
PRICE_BUCKETS = (
    (0, "до 10 000 т"),
    (10_000, "10 000 - 49 999 т"),
    (50_000, "50 000 - 99 999 т"),
    (100_000, "100 000 - 149 999 т"),
    (150_000, "150 000 - 199 999 т"),
    (200_000, "200 000 - 499 999 т"),
    (500_000, "более 500 000 т"),
)


class KaspiClient:
    """Thin async wrapper around the Kaspi.kz internal API."""

    def __init__(
        self,
        city: str = DEFAULT_CITY,
        *,
        proxy: str | None = None,
        http: PoliteClient | None = None,
    ) -> None:
        self.city = city
        self.http = http or PoliteClient(
            user_agent=USER_AGENT,
            headers={
                "accept": "application/json, text/*",
                "x-requested-with": "XMLHttpRequest",
            },
            max_concurrency=MAX_CONCURRENCY,
            min_delay_s=MIN_DELAY_S,
            max_delay_s=MAX_DELAY_S,
            timeout_s=TIMEOUT_S,
            max_retries=MAX_RETRIES,
            proxy=proxy if proxy is not None else os.environ.get(PROXY_ENV_VAR) or None,
        )

    def _city_headers(self) -> dict[str, str]:
        return {
            "x-ks-city": self.city,
            "cookie": f"kaspi.storefront.cookie.city={self.city}",
        }

    def _product_referer(self, product_id: str) -> str:
        return f"{BASE_URL}/shop/p/-{product_id}/?c={self.city}"

    async def search(
        self, query: str, page: int = 0, sort: str = "relevance", budget: int | None = None
    ) -> Any:
        """Call ``GET /yml/product-view/pl/filters`` and return decoded JSON.

        ``budget`` becomes Kaspi's price filter (``q=:price:<bucket>...``), see
        ``PRICE_BUCKETS``. A budget that every bucket fits sends no filter.
        """
        params: dict[str, object] = {
            "text": query,
            "page": page,
            "c": self.city,
            "ui": "d",
            "sort": sort,
        }
        if budget is not None and budget < PRICE_BUCKETS[-1][0]:
            params["q"] = "".join(
                f":price:{label}" for low, label in PRICE_BUCKETS if low <= max(budget, 0)
            )
        return await self.http.get_json(
            f"{BASE_URL}/yml/product-view/pl/filters",
            params=params,
            referer=f"{BASE_URL}/shop/search/",
            headers=self._city_headers(),
        )

    async def offers(self, product_id: str, limit: int = 32, page: int = 0) -> Any:
        """Call ``POST /yml/offer-view/offers/{id}`` and return decoded JSON.

        A POST in name only — it is a read — so ``retry=True`` opts it back into
        the retry policy that ``PoliteClient`` withholds from non-idempotent
        methods by default.
        """
        body = {
            "cityId": self.city,
            "id": str(product_id),
            "merchantUID": "",
            "limit": limit,
            "page": page,
            "sort": True,
            "installationId": "-1",
        }
        return await self.http.post_json(
            f"{BASE_URL}/yml/offer-view/offers/{product_id}",
            json=body,
            referer=self._product_referer(product_id),
            headers=self._city_headers(),
            retry=True,
        )

    async def reviews(
        self,
        product_id: str,
        limit: int = 9,
        filter: str = "COMMENT",  # noqa: A002 — mirrors the API's own param name
        sort: str = "POPULARITY",
    ) -> Any:
        """Call ``GET /yml/review-view/api/v1/reviews/product/{id}`` (decoded JSON)."""
        params = {"filter": filter, "sort": sort, "limit": limit, "withAgg": "true"}
        return await self.http.get_json(
            f"{BASE_URL}/yml/review-view/api/v1/reviews/product/{product_id}",
            params=params,
            referer=self._product_referer(product_id),
            headers=self._city_headers(),
        )

    async def specs(self, product_id: str) -> Any:
        """Call ``GET /yml/content/item/product/{id}`` and return decoded JSON."""
        return await self.http.get_json(
            f"{BASE_URL}/yml/content/item/product/{product_id}",
            params={"c": self.city},
            referer=self._product_referer(product_id),
            headers=self._city_headers(),
        )

    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self.http.aclose()


__all__ = ["BASE_URL", "DEFAULT_CITY", "PRICE_BUCKETS", "USER_AGENT", "KaspiClient"]
