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
from pathlib import Path
from typing import Any
from urllib.parse import quote

from market_sources.playwright_session import (
    BrowserSession,
    BrowserSessionError,
    NavBlocked,
    SessionExpired,
)

BASE_URL = "https://www.ozon.kz"
BASE_URL_ENV_VAR = "OZON_BASE_URL"
STATE_PATH_ENV_VAR = "OZON_STATE_PATH"
DEFAULT_STATE_PATH = Path.home() / ".config" / "market_sources" / "ozon_state.json"
CHALLENGE_WAIT_MS = 15000  # let the JS challenge set cookies on first home load

# Only challenge-page phrases: a bare "доступ" also matches "Доступные цены…".
_ANTIBOT_RE = re.compile(r"antibot|доступ\w* ограничен|ограничен\w* доступ|нет доступа", re.I)


class OzonError(BrowserSessionError):
    """Raised when Ozon returns a status that a relaunch cannot fix."""


def ozon_state_path() -> Path:
    """Where the Ozon session is saved: ``$OZON_STATE_PATH`` or the default file."""
    raw = os.environ.get(STATE_PATH_ENV_VAR)
    return Path(raw).expanduser() if raw else DEFAULT_STATE_PATH


class OzonBlocked(NavBlocked, OzonError):
    """Ozon answered a blocked status (401/403/429/5xx...) after retries."""


class OzonSessionExpired(SessionExpired, OzonBlocked):
    """The saved Ozon session was rejected: run ``buysmarter ozon-setup``."""


class OzonBrowser(BrowserSession):
    """Reusable Chromium session issuing composer-api navigations."""

    error_class = OzonError
    blocked_class = OzonBlocked
    expired_class = OzonSessionExpired

    def __init__(
        self, base_url: str | None = None, state_path: Path | None = None, **kwargs: Any
    ) -> None:
        # Saved session is used only when its file exists; otherwise anonymous.
        if state_path is None:
            default = ozon_state_path()
            state_path = default if default.exists() else None
        super().__init__(state_path=state_path, **kwargs)
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


__all__ = [
    "BASE_URL",
    "CHALLENGE_WAIT_MS",
    "OzonBlocked",
    "OzonBrowser",
    "OzonError",
    "OzonSessionExpired",
    "ozon_state_path",
]
