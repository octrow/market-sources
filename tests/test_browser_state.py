"""Persisted session state, bootstrap, and nav-failure classification (no browser).

Playwright is replaced by a recording fake API installed on ``_pw_api``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fakes import FakePage, PageFactory

import market_sources.playwright_session as mod
from market_sources.ozon.browser import STATE_PATH_ENV_VAR, OzonBrowser
from market_sources.playwright_session import (
    BrowserSession,
    BrowserSessionError,
    NavBlocked,
    SessionExpired,
)

SAVED_UA = "Mozilla/5.0 (saved) Chrome/130"


class FakeRequest:
    def __init__(self, statuses: list[int]) -> None:
        self.statuses = list(statuses)

    async def get(self, url: str):
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return type("Resp", (), {"status": status})()


class FakeContext:
    def __init__(self, options: dict, probe_statuses: list[int]) -> None:
        self.options = options
        self.saved_to: list[str] = []
        self.closed = False
        self._probe = FakeRequest(probe_statuses)
        self.page = FakePage(name="boot")
        self.page.request = self._probe

    async def new_page(self):
        return self.page

    async def storage_state(self, path: str) -> dict:
        self.saved_to.append(path)
        Path(path).write_text(json.dumps({"cookies": [], "origins": []}))
        return {"cookies": [], "origins": []}

    async def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self, launch_kwargs: dict, probe_statuses: list[int]) -> None:
        self.launch_kwargs = launch_kwargs
        self.probe_statuses = probe_statuses
        self.contexts: list[FakeContext] = []
        self.closed = False

    def is_connected(self) -> bool:
        return True

    def on(self, _event, _cb) -> None:
        pass

    async def new_context(self, **options) -> FakeContext:
        ctx = FakeContext(options, self.probe_statuses)
        self.contexts.append(ctx)
        return ctx

    async def close(self) -> None:
        self.closed = True


class FakeApi:
    """Stands in for ``playwright.async_api``; records every browser it launches."""

    def __init__(self, probe_statuses: list[int] | None = None) -> None:
        self.probe_statuses = probe_statuses or [200]
        self.browsers: list[FakeBrowser] = []
        self.drivers_stopped = 0

    def async_playwright(self):
        return self

    async def start(self):
        return self

    @property
    def chromium(self):
        return self

    async def launch(self, headless: bool, args: list[str]) -> FakeBrowser:
        browser = FakeBrowser({"headless": headless, "args": args}, self.probe_statuses)
        self.browsers.append(browser)
        return browser

    async def stop(self) -> None:
        self.drivers_stopped += 1


@pytest.fixture
def fake_api(monkeypatch):
    api = FakeApi()
    monkeypatch.setattr(mod, "_pw_api", api)
    return api


def write_state(path: Path, ua: str | None = SAVED_UA) -> Path:
    path.write_text(json.dumps({"cookies": [], "origins": []}))
    if ua is not None:
        Path(f"{path}{mod.UA_SUFFIX}").write_text(json.dumps({"user_agent": ua}))
    return path


# ------------------------------------------------------------- context options


async def test_no_state_path_passes_no_storage_state(fake_api, tmp_path):
    sess = BrowserSession(idle_timeout_s=0)
    await sess._open_page()
    options = fake_api.browsers[0].contexts[0].options
    assert "storage_state" not in options
    assert options["user_agent"] == mod.DEFAULT_USER_AGENT
    await sess.aclose()


async def test_existing_state_file_is_passed_with_its_saved_user_agent(fake_api, tmp_path):
    state = write_state(tmp_path / "state.json")
    sess = BrowserSession(idle_timeout_s=0, state_path=state)
    await sess._open_page()
    options = fake_api.browsers[0].contexts[0].options
    assert options["storage_state"] == str(state)
    assert options["user_agent"] == SAVED_UA
    await sess.aclose()


async def test_missing_state_file_is_not_passed(fake_api, tmp_path):
    sess = BrowserSession(idle_timeout_s=0, state_path=tmp_path / "absent.json")
    await sess._open_page()
    assert "storage_state" not in fake_api.browsers[0].contexts[0].options
    await sess.aclose()


async def test_state_without_saved_user_agent_falls_back_to_the_default(fake_api, tmp_path):
    state = write_state(tmp_path / "state.json", ua=None)
    sess = BrowserSession(idle_timeout_s=0, state_path=state)
    await sess._open_page()
    options = fake_api.browsers[0].contexts[0].options
    assert options["storage_state"] == str(state)
    assert options["user_agent"] == mod.DEFAULT_USER_AGENT
    await sess.aclose()


# ------------------------------------------------------------------ bootstrap


async def test_bootstrap_without_state_path_is_a_value_error():
    sess = BrowserSession(page_factory=PageFactory(FakePage()))
    with pytest.raises(ValueError, match="state_path"):
        await sess.bootstrap("https://x.test/probe")


async def test_bootstrap_polls_until_ok_then_saves_state_and_user_agent(fake_api, tmp_path):
    fake_api.probe_statuses = [403, 403, 200]
    state = tmp_path / "nested" / "state.json"
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    sess = BrowserSession(state_path=state, sleep=sleep, user_agent=SAVED_UA)
    assert await sess.bootstrap("https://x.test/probe", poll_s=2.0) is True

    [browser] = fake_api.browsers
    assert browser.launch_kwargs["headless"] is False
    assert "--disable-blink-features=AutomationControlled" in browser.launch_kwargs["args"]
    [ctx] = browser.contexts
    assert "storage_state" not in ctx.options
    assert ctx.saved_to == [str(state)]
    assert json.loads(state.read_text()) == {"cookies": [], "origins": []}
    assert json.loads(Path(f"{state}{mod.UA_SUFFIX}").read_text()) == {"user_agent": SAVED_UA}
    assert sleeps == [2.0, 2.0]
    assert browser.closed is True
    assert fake_api.drivers_stopped == 1


async def test_bootstrap_times_out_without_writing_state(fake_api, tmp_path):
    fake_api.probe_statuses = [403]
    state = tmp_path / "state.json"

    async def sleep(_delay: float) -> None:
        pass

    sess = BrowserSession(state_path=state, sleep=sleep)
    assert await sess.bootstrap("https://x.test/probe", timeout_s=3, poll_s=1) is False
    assert not state.exists()
    assert fake_api.browsers[0].closed is True


# ------------------------------------------------------ nav failure classes


async def test_persistent_blocked_status_raises_nav_blocked():
    sess = BrowserSession(page_factory=PageFactory(FakePage(script=[503])), idle_timeout_s=0)
    with pytest.raises(NavBlocked, match="HTTP 503") as info:
        await sess.fetch_text("https://x.test/")
    assert info.value.status == 503
    assert info.value.url == "https://x.test/"
    assert not isinstance(info.value, SessionExpired)


async def test_forbidden_without_state_path_is_nav_blocked_not_expired():
    sess = BrowserSession(page_factory=PageFactory(FakePage(script=[403])), idle_timeout_s=0)
    with pytest.raises(NavBlocked) as info:
        await sess.fetch_text("https://x.test/", retries=0)
    assert not isinstance(info.value, SessionExpired)


@pytest.mark.parametrize("status", [403, 401, 307])
async def test_rejected_status_with_state_path_is_session_expired(tmp_path, status):
    sess = BrowserSession(
        page_factory=PageFactory(FakePage(script=[status])),
        idle_timeout_s=0,
        state_path=tmp_path / "state.json",
    )
    with pytest.raises(SessionExpired, match="setup command") as info:
        await sess.fetch_text("https://x.test/")
    assert info.value.status == status


async def test_redirect_status_without_state_path_stays_a_plain_error():
    sess = BrowserSession(page_factory=PageFactory(FakePage(script=[307])), idle_timeout_s=0)
    with pytest.raises(BrowserSessionError) as info:
        await sess.fetch_text("https://x.test/", retries=0)
    assert not isinstance(info.value, NavBlocked)


async def test_not_found_is_not_classified_as_blocked(tmp_path):
    sess = BrowserSession(
        page_factory=PageFactory(FakePage(script=[404])),
        idle_timeout_s=0,
        state_path=tmp_path / "state.json",
    )
    with pytest.raises(BrowserSessionError) as info:
        await sess.fetch_text("https://x.test/")
    assert not isinstance(info.value, NavBlocked)


# --------------------------------------------------------------- ozon wiring


def test_ozon_uses_env_state_path_only_when_the_file_exists(monkeypatch, tmp_path):
    state = write_state(tmp_path / "ozon.json", ua=None)
    monkeypatch.setenv(STATE_PATH_ENV_VAR, str(state))
    assert OzonBrowser().state_path == state

    monkeypatch.setenv(STATE_PATH_ENV_VAR, str(tmp_path / "missing.json"))
    assert OzonBrowser().state_path is None


def test_ozon_explicit_state_path_wins(tmp_path):
    explicit = tmp_path / "explicit.json"
    assert OzonBrowser(state_path=explicit).state_path == explicit
