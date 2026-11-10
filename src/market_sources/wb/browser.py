"""The user's own Chrome, reached over CDP, reading Wildberries' search answer.

WB refuses scripted HTTP and Playwright-launched browsers (HTTP 403 / 498, checked
2026-11-11). A Chrome the user started themselves passes as any visit does:

    google-chrome --remote-debugging-port=9222 --user-data-dir=~/.config/buysmarter/chrome-wb

Each search opens a tab on the site's search page, keeps the site's own search answer
(the JSON its page asks for) and closes the tab. The user's browser is never closed.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from urllib.parse import quote

from market_sources.wb.parse import BASE_URL

CDP_URL_ENV_VAR = "WB_CDP_URL"
DEFAULT_CDP_URL = "http://127.0.0.1:9222"
ANSWER_TIMEOUT_S = 20.0
_SEARCH_MARK = "/exactmatch/"
START_HINT = (
    "WB читается через ваш Chrome: запустите google-chrome --remote-debugging-port=9222 "
    "--user-data-dir=~/.config/buysmarter/chrome-wb"
)


class WbUnavailable(RuntimeError):
    """No Chrome to read WB with, or WB answered with its block page."""


def search_url(query: str) -> str:
    return f"{BASE_URL}/catalog/0/search.aspx?search={quote(query)}"


class WbBrowser:
    """A lazy CDP connection to the user's Chrome; one search tab at a time."""

    def __init__(self, cdp_url: str | None = None) -> None:
        self.cdp_url = cdp_url or os.environ.get(CDP_URL_ENV_VAR) or DEFAULT_CDP_URL
        self._pw: Any = None
        self._browser: Any = None
        self._lock = asyncio.Lock()

    async def _context(self) -> Any:
        if self._browser is None or not self._browser.is_connected():
            from playwright.async_api import async_playwright

            self._pw = self._pw or await async_playwright().start()
            try:
                self._browser = await self._pw.chromium.connect_over_cdp(self.cdp_url)
            except Exception as exc:  # noqa: BLE001 - any failure means "no Chrome there"
                raise WbUnavailable(f"{START_HINT} ({type(exc).__name__})") from exc
        return self._browser.contexts[0]

    async def search_json(self, query: str) -> tuple[str, dict]:
        """``(answer url, answer JSON)`` of the site's own search for ``query``."""
        async with self._lock:
            page = await (await self._context()).new_page()
            answers: list[tuple[str, dict]] = []

            async def keep(response: Any) -> None:
                if _SEARCH_MARK in response.url and response.status == 200:
                    try:
                        answers.append((response.url, json.loads(await response.text())))
                    except ValueError:
                        pass

            page.on("response", keep)
            try:
                nav = await page.goto(search_url(query), wait_until="domcontentloaded")
                if nav is not None and nav.status != 200:
                    raise WbUnavailable(f"WB answered HTTP {nav.status} (block page)")
                return await _first_with_products(answers)
            finally:
                await page.close()

    async def aclose(self) -> None:
        """Disconnect from the user's Chrome without closing it."""
        if self._browser is not None:
            await self._browser.close()  # connected over CDP: this only disconnects
        if self._pw is not None:
            await self._pw.stop()
        self._browser = self._pw = None


async def _first_with_products(answers: list[tuple[str, dict]]) -> tuple[str, dict]:
    """Wait for the search answer that has products; the page also asks for empty ones."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + ANSWER_TIMEOUT_S
    while loop.time() < deadline:
        for url, payload in answers:
            if payload.get("products"):
                return url, payload
        await asyncio.sleep(0.25)
    if answers:
        return answers[0]  # the search found nothing
    raise WbUnavailable("WB sent no search answer in time")
