"""BrowserSession: lazy playwright import, nav lock, relaunch-retry, idle timer.

Driven entirely through an injected ``page_factory``, so no browser is ever
launched and playwright need not even be installed.
"""

from __future__ import annotations

import asyncio
import importlib
import sys

import pytest
from fakes import FakePage, PageFactory, RecordingSleep

from market_sources.playwright_session import BrowserSession, BrowserSessionError


def session(*pages: FakePage, **kwargs) -> tuple[BrowserSession, PageFactory]:
    factory = PageFactory(*pages)
    kwargs.setdefault("idle_timeout_s", 0)
    return BrowserSession(page_factory=factory, **kwargs), factory


# ----------------------------------------------------------------- lazy import


class BlockPlaywright:
    """Meta-path finder that makes any ``playwright`` import raise ImportError."""

    def find_spec(self, name, path=None, target=None):
        if name == "playwright" or name.startswith("playwright."):
            raise ImportError(f"No module named {name!r} (blocked by test)")
        return None


@pytest.fixture
def no_playwright(monkeypatch):
    monkeypatch.setattr(sys, "meta_path", [BlockPlaywright(), *sys.meta_path])
    for name in [n for n in sys.modules if n.split(".")[0] == "playwright"]:
        monkeypatch.delitem(sys.modules, name)
    for name in [n for n in sys.modules if n.split(".")[0] == "market_sources"]:
        monkeypatch.delitem(sys.modules, name)
    yield
    importlib.invalidate_caches()


def test_playwright_is_really_blocked(no_playwright):
    with pytest.raises(ImportError):
        importlib.import_module("playwright")


def test_modules_import_without_playwright(no_playwright):
    """The load-bearing invariant: the `browser` extra is needed to *run*, not to import."""
    for name in ("market_sources", "market_sources.playwright_session", "market_sources.ozon.source"):
        assert importlib.import_module(name) is not None


async def test_a_session_runs_without_playwright_when_a_page_is_injected(no_playwright):
    mod = importlib.import_module("market_sources.playwright_session")
    fakes = importlib.import_module("fakes")
    page = fakes.FakePage(body="hello")
    sess = mod.BrowserSession(page_factory=PageFactory(page), idle_timeout_s=0)
    assert await sess.fetch_text("https://x.test/") == "hello"


# -------------------------------------------------------------------- happy path


async def test_fetch_text_returns_the_body_and_reuses_the_page():
    sess, factory = session(FakePage(body="payload"))
    assert await sess.fetch_text("https://x.test/a") == "payload"
    assert await sess.fetch_text("https://x.test/b") == "payload"
    assert len(factory.opened) == 1
    assert factory.opened[0].gotos == ["https://x.test/a", "https://x.test/b"]


async def test_nav_timeout_is_passed_through():
    class Recorder(FakePage):
        def __init__(self):
            super().__init__()
            self.timeouts: list[int] = []

        async def goto(self, url, wait_until=None, timeout=None):
            self.timeouts.append(timeout)
            return await super().goto(url, wait_until, timeout)

    page = Recorder()
    sess, _ = session(page, nav_timeout_ms=1234)
    await sess.fetch_text("https://x.test/")
    assert page.timeouts == [1234]


# ---------------------------------------------------------------------- page_op


async def test_page_op_hands_the_live_page_to_the_callback():
    page = FakePage(body="ignored")
    sess, _ = session(page)

    async def op(p):
        return await p.title()

    assert await sess.page_op("https://x.test/", op) == "Ozon"
    assert page.gotos == ["https://x.test/"]


async def test_page_op_retries_relaunch_statuses_like_fetch_text():
    sess, factory = session(FakePage(script=[403], name="p1"), FakePage(name="p2", body="ok"))

    async def op(p):
        return p.name

    assert await sess.page_op("https://x.test/", op) == "p2"
    assert [p.name for p in factory.opened] == ["p1", "p2"]


def test_is_dead_browser_error_classification():
    dead = RuntimeError("Target page, context or browser has been closed")
    assert BrowserSession.is_dead_browser_error(dead)
    assert BrowserSession.is_dead_browser_error(RuntimeError("browser crashed"))
    assert not BrowserSession.is_dead_browser_error(ValueError("selector is wrong"))


# ---------------------------------------------------------------------- nav lock


async def test_navigations_are_serialized_behind_the_lock():
    trace: list[str] = []
    page = FakePage(body="ok", trace=trace)
    sess, _ = session(page)
    await asyncio.gather(*(sess.fetch_text(f"https://x.test/{i}") for i in range(3)))
    assert trace == ["page:start", "page:end"] * 3


# ----------------------------------------------------------------- ready hook


async def test_prepare_page_runs_once_per_page():
    calls: list[str] = []

    class Prepared(BrowserSession):
        async def prepare_page(self, page):
            calls.append(page.name)

    factory = PageFactory(FakePage(name="p1"), FakePage(name="p2"))
    sess = Prepared(page_factory=factory, idle_timeout_s=0)
    await sess.fetch_text("https://x.test/1")
    await sess.fetch_text("https://x.test/2")
    assert calls == ["p1"]
    await sess.aclose()
    await sess.fetch_text("https://x.test/3")
    assert calls == ["p1", "p2"]


async def test_a_failing_ready_hook_propagates():
    class Broken(BrowserSession):
        async def prepare_page(self, page):
            raise BrowserSessionError("challenge not passed")

    sess = Broken(page_factory=PageFactory(FakePage()), idle_timeout_s=0)
    with pytest.raises(BrowserSessionError, match="challenge"):
        await sess.fetch_text("https://x.test/")


async def test_a_failing_ready_hook_closes_the_page_it_opened():
    class Broken(BrowserSession):
        async def prepare_page(self, page):
            raise BrowserSessionError("challenge not passed")

    factory = PageFactory(FakePage(name="p1"), FakePage(name="p2"))
    sess = Broken(page_factory=factory, idle_timeout_s=0)
    for _ in range(2):
        with pytest.raises(BrowserSessionError):
            await sess.fetch_text("https://x.test/")
    assert [p.closed for p in factory.opened] == [True, True]


# ------------------------------------------------------------- relaunch / retry


async def test_a_relaunch_status_tears_down_and_retries_once():
    sess, factory = session(FakePage(script=[403], name="p1"), FakePage(body="fresh", name="p2"))
    assert await sess.fetch_text("https://x.test/") == "fresh"
    assert [p.name for p in factory.opened] == ["p1", "p2"]
    assert factory.opened[0].closed is True


async def test_a_relaunch_status_that_never_clears_raises():
    sess, factory = session(FakePage(script=[403]))
    with pytest.raises(BrowserSessionError, match="HTTP 403"):
        await sess.fetch_text("https://x.test/")
    assert len(factory.opened) == 2  # one retry, then give up


async def test_a_plain_error_status_is_not_retried():
    sess, factory = session(FakePage(script=[404]))
    with pytest.raises(BrowserSessionError, match="HTTP 404"):
        await sess.fetch_text("https://x.test/")
    assert len(factory.opened) == 1


async def test_no_response_object_is_reported_as_status_minus_one():
    sess, _ = session(FakePage(script=[None]))
    with pytest.raises(BrowserSessionError, match="HTTP -1"):
        await sess.fetch_text("https://x.test/")


async def test_a_dead_browser_error_triggers_a_relaunch():
    sess, factory = session(
        FakePage(script=[RuntimeError("Target page, context or browser has been closed")]),
        FakePage(body="revived"),
    )
    assert await sess.fetch_text("https://x.test/") == "revived"
    assert len(factory.opened) == 2


async def test_an_unrelated_error_is_not_retried():
    sess, factory = session(FakePage(script=[ValueError("selector is wrong")]))
    with pytest.raises(ValueError, match="selector"):
        await sess.fetch_text("https://x.test/")
    assert len(factory.opened) == 1


async def test_retries_can_be_disabled_per_call():
    sess, factory = session(FakePage(script=[403]), FakePage(body="fresh"))
    with pytest.raises(BrowserSessionError, match="HTTP 403"):
        await sess.fetch_text("https://x.test/", retries=0)
    assert len(factory.opened) == 1


# ------------------------------------------------------------------- teardown


async def test_aclose_is_idempotent_and_relaunches_lazily():
    sess, factory = session(FakePage(name="p1"), FakePage(name="p2"))
    await sess.fetch_text("https://x.test/")
    await sess.aclose()
    await sess.aclose()
    assert factory.opened[0].closed is True
    await sess.fetch_text("https://x.test/")
    assert [p.name for p in factory.opened] == ["p1", "p2"]


async def test_the_idle_timer_closes_the_session_only_after_the_timeout():
    gate = asyncio.Event()
    recorded: list[float] = []

    async def gated_sleep(delay: float) -> None:
        recorded.append(delay)
        await gate.wait()

    page = FakePage()
    sess = BrowserSession(page_factory=PageFactory(page), idle_timeout_s=600, sleep=gated_sleep)
    await sess.fetch_text("https://x.test/")
    assert recorded == [600]
    assert page.closed is False  # still parked on the timer
    gate.set()
    for _ in range(5):  # let the fire-and-forget idle task finish
        await asyncio.sleep(0)
    assert page.closed is True
    assert sess._page is None


async def test_a_new_request_resets_the_idle_timer():
    recorded: list[float] = []

    async def gated_sleep(delay: float) -> None:
        recorded.append(delay)
        await asyncio.Event().wait()  # never fires

    page = FakePage()
    sess = BrowserSession(page_factory=PageFactory(page), idle_timeout_s=600, sleep=gated_sleep)
    await sess.fetch_text("https://x.test/1")
    await sess.fetch_text("https://x.test/2")
    assert recorded == [600, 600]  # the first timer was cancelled and re-armed
    assert page.closed is False
    await sess.aclose()


async def test_a_zero_idle_timeout_schedules_nothing():
    sleep = RecordingSleep()
    sess, _ = session(FakePage(), idle_timeout_s=0, sleep=sleep)
    await sess.fetch_text("https://x.test/")
    assert sleep.calls == []
    assert sess._idle_task is None


# ------------------------------------------------------- playwright driver leak


async def test_a_crashed_browser_relaunch_stops_the_old_playwright_driver(monkeypatch):
    import market_sources.playwright_session as mod

    drivers: list = []

    class FakeContext:
        async def new_page(self):
            return FakePage()

        async def close(self):
            pass

    class FakeBrowser:
        def is_connected(self):
            return True

        def on(self, _event, _cb):
            pass

        async def new_context(self, **_kw):
            return FakeContext()

        async def close(self):
            pass

    class FakeDriver:
        def __init__(self):
            self.stopped = False
            self.chromium = self

        async def launch(self, **_kw):
            return FakeBrowser()

        async def stop(self):
            self.stopped = True

    class FakeStarter:
        async def start(self):
            drivers.append(FakeDriver())
            return drivers[-1]

    class FakeApi:
        @staticmethod
        def async_playwright():
            return FakeStarter()

    monkeypatch.setattr(mod, "_pw_api", FakeApi())
    sess = BrowserSession(idle_timeout_s=0)
    await sess._open_page()
    sess._on_disconnect(None)  # browser crashed; the driver is still running
    await sess._open_page()
    assert [d.stopped for d in drivers] == [True, False]
    await sess.aclose()
    assert drivers[1].stopped is True
