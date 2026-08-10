"""Test doubles: no network, no browser, no clock.

Everything time-related in the library is injectable, so these fakes are enough
to drive the pacing/retry state machines deterministically.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx


class LowRng:
    """``uniform(a, b) -> a`` — the minimum of every jitter range."""

    def uniform(self, a: float, b: float) -> float:
        return a


class HighRng:
    """``uniform(a, b) -> b`` — the maximum of every jitter range."""

    def uniform(self, a: float, b: float) -> float:
        return b


class RecordingSleep:
    """An ``asyncio.sleep`` stand-in that records durations and never waits."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.calls.append(delay)
        await asyncio.sleep(0)


class ScriptedTransport(httpx.AsyncBaseTransport):
    """Replays a script of responses/exceptions and records what was sent.

    ``script`` entries are either an ``httpx.Response``, an ``Exception`` to
    raise, or a callable ``(request) -> Response``. The last entry repeats once
    the script runs out.
    """

    def __init__(self, script: list[Any] | None = None) -> None:
        self.script = list(script or [httpx.Response(200, json={})])
        self.requests: list[httpx.Request] = []
        self.inflight = 0
        self.max_inflight = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            await asyncio.sleep(0)  # a real yield, so overlap is observable
            entry = self.script.pop(0) if len(self.script) > 1 else self.script[0]
            if isinstance(entry, Exception):
                raise entry
            if callable(entry):
                return entry(request)
            return entry
        finally:
            self.inflight -= 1


class FakeResponse:
    """Minimal ``playwright`` response stand-in."""

    def __init__(self, status: int) -> None:
        self.status = status


class FakePage:
    """Minimal ``playwright`` page stand-in with a scripted ``goto``."""

    def __init__(
        self,
        script: list[Any] | None = None,
        body: str = "BODY",
        title: str = "Ozon",
        trace: list[str] | None = None,
        name: str = "page",
    ) -> None:
        self.script = list(script or [200])
        self.body = body
        self._title = title
        self.gotos: list[str] = []
        self.waits: list[int] = []
        self.closed = False
        self.trace = trace if trace is not None else []
        self.name = name

    async def goto(self, url: str, wait_until: str | None = None, timeout: int | None = None):
        self.gotos.append(url)
        self.trace.append(f"{self.name}:start")
        await asyncio.sleep(0)
        self.trace.append(f"{self.name}:end")
        entry = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(entry, Exception):
            raise entry
        if entry is None:
            return None
        return FakeResponse(entry)

    async def evaluate(self, _script: str) -> str:
        return self.body

    async def title(self) -> str:
        return self._title

    async def wait_for_timeout(self, ms: int) -> None:
        self.waits.append(ms)

    async def close(self) -> None:
        self.closed = True


class PageFactory:
    """Hands out pre-built ``FakePage``s and records how many were opened."""

    def __init__(self, *pages: FakePage) -> None:
        self.pages = list(pages)
        self.opened: list[FakePage] = []

    async def __call__(self) -> FakePage:
        page = self.pages.pop(0) if len(self.pages) > 1 else self.pages[0]
        self.opened.append(page)
        return page
