"""``BrowserSession`` — a lazy, long-lived headless Chromium session.

Generalized from buy-smarter's ``OzonBrowser`` (itself ported from
``ozon-mcp-server``, MIT), with the session-handling half of
olx-almaty-phone-analyzer's ``KimovilScraperV2`` folded in. Both wanted the same
four things; only Ozon actually got them right:

* **lazy launch** — importing this module must not start a browser, and
  ``playwright`` itself is imported *inside* :meth:`_open_page`, so the module
  imports fine without the ``browser`` extra installed;
* **serialized navigation** — one shared page means every navigation mutates
  global state, so they queue behind an ``asyncio.Lock``;
* **a ready-check hook** — :meth:`prepare_page` runs once per fresh page
  (pass an anti-bot challenge, log in, set a cookie) and its result is cached
  until the session is torn down;
* **retry by relaunch** — a status in :attr:`relaunch_statuses` (Ozon's Variti
  wall answers 403/307 when the challenge cookie went stale) or a
  browser-is-dead error tears the session down and tries once more.

Plus an **idle timer**: a browser sitting on 300 MB of RAM for nothing gets
closed after ``idle_timeout_s`` and lazily relaunched on the next call.

Subclass it, or inject ``page_factory`` (a zero-arg coroutine returning
something page-shaped) to drive the whole state machine without a real browser.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger(__name__)

#: Chromium flags that keep automation cheap and less detectable. Note there is
#: deliberately **no** request-blocking of images/css/fonts here: anti-bot
#: challenges load their assets through them, and blocking earns a 403.
LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--mute-audio",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-extensions",
    "--disable-background-networking",
]
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
DEFAULT_VIEWPORT = {"width": 1920, "height": 1080}
NAV_TIMEOUT_MS = 90_000
IDLE_TIMEOUT_S = 10 * 60

# "Target page, context or browser has been closed", crashes, disconnects.
_DEAD_RE = re.compile(r"closed|crashed|disconnect", re.I)

_pw_api: Any = None


def _playwright() -> Any:
    """Import (once) and return ``playwright.async_api``.

    Kept out of module scope on purpose: ``market_sources`` must import without
    the ``browser`` extra, so only code paths that actually drive a browser pay
    for playwright.
    """
    global _pw_api
    if _pw_api is None:
        from playwright import async_api

        _pw_api = async_api
    return _pw_api


class BrowserSessionError(RuntimeError):
    """Raised when a navigation fails in a way relaunching cannot fix."""


class BrowserSession:
    """One reusable Chromium page, guarded by a lock and an idle timer."""

    #: Statuses that mean "the session went stale" rather than "no such page".
    relaunch_statuses: frozenset[int] = frozenset({403, 307})
    #: Exception type raised for unrecoverable navigations. Override in a
    #: subclass to give consumers a site-specific error without wrapping.
    error_class: type[BrowserSessionError] = BrowserSessionError

    def __init__(
        self,
        *,
        headless: bool = True,
        user_agent: str = DEFAULT_USER_AGENT,
        locale: str = "ru-RU",
        viewport: dict[str, int] | None = None,
        launch_args: list[str] | None = None,
        nav_timeout_ms: int = NAV_TIMEOUT_MS,
        idle_timeout_s: float = IDLE_TIMEOUT_S,
        page_factory: Callable[[], Awaitable[Any]] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.headless = headless
        self.user_agent = user_agent
        self.locale = locale
        self.viewport = dict(viewport or DEFAULT_VIEWPORT)
        self.launch_args = list(launch_args if launch_args is not None else LAUNCH_ARGS)
        self.nav_timeout_ms = nav_timeout_ms
        self.idle_timeout_s = idle_timeout_s
        self._page_factory = page_factory
        self._sleep = sleep
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._ready = False
        self._lock = asyncio.Lock()
        self._idle_task: asyncio.Task[None] | None = None

    # -------------------------------------------------------------- public API

    async def fetch_text(self, url: str, *, retries: int = 1) -> str:
        """Navigate to ``url`` and return ``document.body.innerText``.

        Serialized against every other navigation on this session. Retries by
        full relaunch on :attr:`relaunch_statuses` and on browser-is-dead
        errors; any other non-200 raises :attr:`error_class`.
        """
        return await self.page_op(url, _body_text, retries=retries)

    async def page_op(self, url: str, op: Callable[[Any], Awaitable[Any]], *,
                      retries: int = 1) -> Any:
        """Navigate to ``url`` and run ``op(page)`` on the live page — locked, retried.

        The primitive for locator-based consumers (kimovil-style extraction)
        that need more than ``innerText``: same nav lock, idle reset, relaunch
        retry and status classification as :meth:`fetch_text`, with the page
        handed to ``op`` only after a 200.
        """
        async with self._lock:
            for attempt in range(retries + 1):
                try:
                    self._reset_idle()
                    await self._ensure_ready()
                    status = await self._goto(url)
                    if status == 200:
                        return await op(self._page)
                    if status in self.relaunch_statuses and attempt < retries:
                        log.debug("browser session: HTTP %d for %s; relaunching", status, url)
                        await self._shutdown()
                        continue
                    raise self.error_class(f"HTTP {status} for {url}")
                except self.error_class:
                    raise
                except Exception as err:  # noqa: BLE001 — classified below
                    if self.is_dead_browser_error(err) and attempt < retries:
                        log.debug("browser session: dead browser (%s); relaunching", err)
                        await self._shutdown()
                        continue
                    raise
            raise self.error_class("unreachable")  # loop always returns or raises

    @staticmethod
    def is_dead_browser_error(err: BaseException) -> bool:
        """True when ``err`` looks like a closed/crashed/disconnected browser."""
        return bool(_DEAD_RE.search(str(err)))

    async def aclose(self) -> None:
        """Tear the session down (idempotent); the next call relaunches lazily."""
        async with self._lock:
            await self._shutdown()

    async def prepare_page(self, page: Any) -> None:
        """Hook: make a freshly opened ``page`` usable. Default: nothing.

        Runs once per page, inside the navigation lock. Override to pass an
        anti-bot challenge, log in, or accept a cookie banner. Raise
        :attr:`error_class` to fail hard, or any dead-browser-looking error to
        get one relaunch retry.
        """

    # ------------------------------------------------------------- state machine

    async def _ensure_ready(self) -> None:
        if self._page is not None and self._ready and self._alive():
            return
        self._page = await self._open_page()
        await self.prepare_page(self._page)
        self._ready = True

    def _alive(self) -> bool:
        if self._browser is None:
            return True  # injected page_factory mode: nothing to disconnect
        return bool(self._browser.is_connected())

    async def _open_page(self) -> Any:
        """Launch Chromium if needed and return a fresh page."""
        if self._page_factory is not None:
            return await self._page_factory()
        pw = _playwright()
        if self._browser is None or not self._browser.is_connected():
            self._pw = await pw.async_playwright().start()
            self._browser = await self._pw.chromium.launch(
                headless=self.headless, args=self.launch_args
            )
            self._browser.on("disconnected", self._on_disconnect)
            self._context = await self._browser.new_context(
                viewport=self.viewport, user_agent=self.user_agent, locale=self.locale
            )
        return await self._context.new_page()

    async def _goto(self, url: str) -> int:
        """``page.goto`` and return the HTTP status (``-1`` when no response object)."""
        page = self._page
        assert page is not None
        resp = await page.goto(
            url, wait_until="domcontentloaded", timeout=self.nav_timeout_ms
        )
        return resp.status if resp else -1

    def _on_disconnect(self, _browser: Any) -> None:
        self._browser = None
        self._context = None
        self._page = None
        self._ready = False

    # ------------------------------------------------------------------- idle

    def _reset_idle(self) -> None:
        if self._idle_task is not None:
            self._idle_task.cancel()
            self._idle_task = None
        if self.idle_timeout_s <= 0:
            return
        self._idle_task = asyncio.ensure_future(self._idle_shutdown())

    async def _idle_shutdown(self) -> None:
        try:
            await self._sleep(self.idle_timeout_s)
        except asyncio.CancelledError:
            return
        # Detach first: `_shutdown` cancels `_idle_task`, and that is *this*
        # task — the buy-smarter original cancelled itself and then awaited
        # `context.close()`, so the CancelledError landed mid-teardown and left
        # the browser process alive. Also take the nav lock, so an idle timeout
        # can never yank the page out from under an in-flight navigation.
        self._idle_task = None
        async with self._lock:
            await self._shutdown()

    async def _shutdown(self) -> None:
        if self._idle_task is not None:
            self._idle_task.cancel()
            self._idle_task = None
        self._ready = False
        page, self._page = self._page, None
        if self._browser is None and page is not None:
            await _close_quietly(page)  # injected mode owns nothing else
        for closable in (self._context, self._browser):
            if closable is not None:
                await _close_quietly(closable)
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception as err:  # noqa: BLE001 — teardown is best-effort
                log.debug("browser session: playwright stop failed: %s", err)
        self._context = self._browser = self._pw = None


async def _body_text(page: Any) -> str:
    return await page.evaluate("() => document.body.innerText")


async def _close_quietly(obj: Any) -> None:
    try:
        await obj.close()
    except Exception as err:  # noqa: BLE001 — teardown is best-effort
        log.debug("browser session: close failed: %s", err)


__all__ = [
    "DEFAULT_USER_AGENT",
    "IDLE_TIMEOUT_S",
    "LAUNCH_ARGS",
    "NAV_TIMEOUT_MS",
    "BrowserSession",
    "BrowserSessionError",
]
