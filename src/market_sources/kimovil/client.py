"""Async client for kimovil.com: the autocomplete JSON and a phone's specs page.

Kimovil sits behind Cloudflare. Headless Chromium gets a 403 "Just a moment..."
challenge, while curl_cffi impersonating Chrome gets the full server-rendered page
(checked 2026-10-13), so requests go through :class:`ImpersonateTransport` when the
``impersonate`` extra is installed. A burst of requests can get the IP challenged for
every client for a while: keep the pacing gentle.
"""

from __future__ import annotations

from typing import Any

from market_sources import impersonate
from market_sources.impersonate import ImpersonateTransport
from market_sources.polite_http import PoliteClient

BASE_URL = "https://www.kimovil.com"
AUTOCOMPLETE_PATH = "/_json/autocomplete_devicemodels_joined.json"
SMARTPHONE_DEVICE_TYPE = 0
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)

MAX_CONCURRENCY = 2
MIN_DELAY_S = 1.0
MAX_DELAY_S = 2.5
TIMEOUT_S = 20.0
MAX_RETRIES = 1


def page_url(slug: str) -> str:
    return f"{BASE_URL}/en/where-to-buy-{slug}"


class KimovilClient:
    """Thin async wrapper around Kimovil's autocomplete endpoint and spec pages."""

    def __init__(self, *, proxy: str | None = None, http: PoliteClient | None = None) -> None:
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

    async def autocomplete(self, name: str) -> Any:
        """Smartphone models matching ``name`` (decoded JSON)."""
        params = {"device_type": SMARTPHONE_DEVICE_TYPE, "name": name}
        return await self.http.get_json(f"{BASE_URL}{AUTOCOMPLETE_PATH}", params=params)

    async def page(self, slug: str) -> str:
        """HTML of the model's specs page."""
        return (await self.http.get(page_url(slug))).text

    async def aclose(self) -> None:
        await self.http.aclose()


__all__ = ["BASE_URL", "KimovilClient", "page_url"]
