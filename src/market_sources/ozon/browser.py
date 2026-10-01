"""Long-lived headless Chromium session for ozon.kz composer-api access.

Ported from ``ozon-mcp-server/src/browser.js`` (MIT) via buy-smarter. A single
Chromium context passes Ozon's anti-bot (Variti) challenge once on first use and
is reused for the whole process; requests hit the internal ``composer-api`` JSON
endpoint.

**Key ozon.kz difference**: the in-page ``fetch()`` transport the ozon.ru
reference uses is blocked on ozon.kz ("TypeError: Failed to fetch"). Requests
therefore go through ``page.goto()`` navigation to the composer-api URL, reading
the JSON off the document body. Navigations mutate the shared page, so they are
serialized — which is exactly what
:class:`market_sources.playwright_session.BrowserSession` provides; this module
only adds the challenge check and the URL shape.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.parse import quote

from market_sources.playwright_session import BrowserSession, BrowserSessionError

BASE_URL = "https://www.ozon.kz"
BASE_URL_ENV_VAR = "OZON_BASE_URL"
CHALLENGE_WAIT_MS = 15000  # let the JS challenge set cookies on first home load

# Only challenge-page phrases: a bare "доступ" also matches "Доступные цены…".
_ANTIBOT_RE = re.compile(r"antibot|доступ\w* ограничен|ограничен\w* доступ|нет доступа", re.I)


class OzonError(BrowserSessionError):
    """Raised when Ozon returns a status that a relaunch cannot fix."""


class OzonBrowser(BrowserSession):
    """Reusable Chromium session issuing composer-api navigations."""

    error_class = OzonError

    def __init__(self, base_url: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.base = (base_url or os.environ.get(BASE_URL_ENV_VAR) or BASE_URL).rstrip("/")

    async def prepare_page(self, page: Any) -> None:
        """Load the home page and wait out the Variti challenge.

        NOTE: images/css/fonts must NOT be route-blocked — the challenge loads
        its assets through them, and blocking makes Ozon answer 403 (which is why
        ``BrowserSession`` has no blocking to begin with).
        """
        await page.goto(
            f"{self.base}/", wait_until="domcontentloaded", timeout=self.nav_timeout_ms
        )
        await page.wait_for_timeout(CHALLENGE_WAIT_MS)
        title = await page.title()
        if _ANTIBOT_RE.search(title or ""):
            raise OzonError(f"anti-bot challenge not passed (title: {title!r})")

    def composer_url(self, path: str) -> str:
        """The composer-api JSON URL for a site ``path`` (e.g. ``/search/?text=x``)."""
        return f"{self.base}/api/composer-api.bx/page/json/v2?url=" + quote(path, safe="")

    async def fetch_json(self, path: str, retries: int = 1) -> dict:
        """Fetch a composer-api page for ``path`` and return the decoded JSON."""
        text = await self.fetch_text(self.composer_url(path), retries=retries)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:  # an HTML challenge/error page, not JSON
            raise OzonError(f"composer-api returned non-JSON ({len(text)} chars)") from exc


__all__ = ["BASE_URL", "CHALLENGE_WAIT_MS", "OzonBrowser", "OzonError"]
