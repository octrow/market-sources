"""``PoliteClient`` — the "be a good citizen" async httpx wrapper.

The same four ingredients were hand-written in four places (buy-smarter's
``KaspiClient`` and ``OlxClient``, olx-almaty-phone-analyzer's
``AsyncOLXScraper``, get_cool_event's ``adapters/sites.py``):

1. a small **semaphore** so a burst of ``asyncio.gather`` never becomes a flood,
2. a **jittered delay** between requests so the pattern does not look robotic,
3. **browser-mimicking headers** (a real Chrome UA — a bare ``python-httpx`` UA
   gets 403'd by every one of these sites; plus ``accept-language`` and an
   optional per-request ``referer``, which is Kaspi's whole anti-bot gate),
4. an optional **proxy** (residential, for when a datacenter IP is throttled).

On top of that it adds the one thing *none* of the four had — **retry with
exponential backoff**:

* retries on transport errors and on ``429`` / ``5xx`` (configurable set),
* honors ``Retry-After`` (seconds *or* HTTP-date) when the server sends one,
* exponential backoff with jitter otherwise, capped by ``backoff_max_s``,
* **never retries a non-idempotent method** (``POST``/``PATCH``) unless the
  caller passes ``retry=True`` for that specific call,
* never retries a 4xx other than 429 — a 404 is an answer, not a hiccup.

Everything time-related is injectable (``sleep``, ``rng``), and the transport
can be replaced, so the whole thing is testable without a network or a clock.
"""

from __future__ import annotations

import asyncio
import email.utils
import logging
import random
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import httpx

log = logging.getLogger(__name__)

# A real desktop Chrome UA. Every site in scope 403s a bare `python-httpx` UA.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)
DEFAULT_ACCEPT = "application/json, text/plain, */*"
DEFAULT_ACCEPT_LANGUAGE = "ru-RU,ru;q=0.9,en;q=0.8"

#: Statuses worth a second try. 429 = rate limited, 5xx = server hiccup.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
#: Methods safe to replay without side effects (RFC 9110 idempotency).
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE", "TRACE"})

SleepFn = Callable[[float], Awaitable[None]]


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """``Retry-After`` (delta-seconds or HTTP-date) as seconds, or ``None``.

    Never returns a negative number: a date already in the past means "retry
    now", i.e. ``0.0``.
    """
    if not value:
        return None
    raw = value.strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - (now or datetime.now(UTC))).total_seconds())


class PoliteClient:
    """A gentle, retrying async HTTP client shared by every source adapter.

    Pacing happens **once per logical request** (before the first attempt);
    retry backoff is additional sleeping on top of it. The underlying
    ``httpx.AsyncClient`` is created lazily on first use and reused — call
    :meth:`aclose`, or use the instance as an async context manager.
    """

    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        headers: dict[str, str] | None = None,
        max_concurrency: int = 2,
        min_delay_s: float = 0.5,
        max_delay_s: float = 1.5,
        timeout_s: float = 10.0,
        proxy: str | None = None,
        follow_redirects: bool = True,
        max_retries: int = 1,
        backoff_base_s: float = 1.0,
        backoff_max_s: float = 30.0,
        backoff_jitter: float = 0.25,
        retry_statuses: frozenset[int] | set[int] = RETRY_STATUSES,
        idempotent_methods: frozenset[str] | set[str] = IDEMPOTENT_METHODS,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: SleepFn = asyncio.sleep,
        rng: random.Random | None = None,
    ) -> None:
        if min_delay_s > max_delay_s:
            raise ValueError("min_delay_s must not exceed max_delay_s")
        if max_retries < 0:
            raise ValueError("max_retries must not be negative")
        self.base_headers: dict[str, str] = {
            "accept": DEFAULT_ACCEPT,
            "accept-language": DEFAULT_ACCEPT_LANGUAGE,
            "user-agent": user_agent,
            **{k.lower(): v for k, v in (headers or {}).items()},
        }
        self.max_concurrency = max_concurrency
        self.min_delay_s = min_delay_s
        self.max_delay_s = max_delay_s
        self.timeout_s = timeout_s
        self.proxy = proxy or None
        self.follow_redirects = follow_redirects
        self.max_retries = max_retries
        self.backoff_base_s = backoff_base_s
        self.backoff_max_s = backoff_max_s
        self.backoff_jitter = backoff_jitter
        self.retry_statuses = frozenset(retry_statuses)
        self.idempotent_methods = frozenset(m.upper() for m in idempotent_methods)
        self._transport = transport
        self._sleep = sleep
        self._rng = rng or random.Random()
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._client: httpx.AsyncClient | None = None

    # ---------------------------------------------------------------- lifecycle

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            kwargs: dict[str, Any] = {
                "timeout": self.timeout_s,
                "follow_redirects": self.follow_redirects,
                "headers": dict(self.base_headers),
            }
            if self._transport is not None:
                kwargs["transport"] = self._transport
            elif self.proxy:
                kwargs["proxy"] = self.proxy
            self._client = httpx.AsyncClient(**kwargs)
        return self._client

    async def aclose(self) -> None:
        """Close the underlying connection pool (idempotent)."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def __aenter__(self) -> PoliteClient:
        self._ensure_client()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------ pacing

    async def pace(self) -> None:
        """Sleep a jittered inter-request delay (no-op when the delay is 0)."""
        if self.max_delay_s <= 0:
            return
        await self._sleep(self._rng.uniform(self.min_delay_s, self.max_delay_s))

    def backoff_delay(self, attempt: int, retry_after: float | None = None) -> float:
        """Seconds to wait before retry number ``attempt`` (0-based).

        A server-supplied ``Retry-After`` wins outright (clamped to
        ``backoff_max_s``); otherwise exponential ``backoff_base_s * 2**attempt``
        plus up to ``backoff_jitter`` of itself, capped at ``backoff_max_s``.
        """
        if retry_after is not None:
            return min(retry_after, self.backoff_max_s)
        base = min(self.backoff_base_s * (2**attempt), self.backoff_max_s)
        return base + self._rng.uniform(0.0, base * self.backoff_jitter)

    # ----------------------------------------------------------------- requests

    def merge_headers(
        self, headers: dict[str, str] | None = None, referer: str | None = None
    ) -> dict[str, str]:
        """Per-request headers layered over the browser-mimicking defaults."""
        merged = dict(self.base_headers)
        if referer is not None:
            merged["referer"] = referer
        for key, value in (headers or {}).items():
            merged[key.lower()] = value
        return merged

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        referer: str | None = None,
        retry: bool | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Issue a paced, retrying request and return a ``raise_for_status``'d response.

        ``retry`` defaults to "yes if the method is idempotent". Pass ``True`` to
        opt a ``POST`` in (some read-only APIs — Kaspi's offers endpoint — are
        POSTs in name only), or ``False`` to opt out entirely.
        """
        method = method.upper()
        allow_retry = (method in self.idempotent_methods) if retry is None else retry
        attempts = self.max_retries + 1 if allow_retry else 1
        request_headers = self.merge_headers(headers, referer)

        async with self._semaphore:
            await self.pace()
            client = self._ensure_client()
            for attempt in range(attempts):
                last = attempt + 1 >= attempts
                try:
                    response = await client.request(
                        method, url, headers=request_headers, **kwargs
                    )
                except httpx.TransportError as exc:
                    if last:
                        raise
                    delay = self.backoff_delay(attempt)
                    log.debug(
                        "polite_http: %s %s transport error (%s); retrying in %.2fs",
                        method,
                        url,
                        exc,
                        delay,
                    )
                    await self._sleep(delay)
                    continue
                if response.status_code in self.retry_statuses and not last:
                    delay = self.backoff_delay(
                        attempt, parse_retry_after(response.headers.get("retry-after"))
                    )
                    log.debug(
                        "polite_http: %s %s -> HTTP %d; retrying in %.2fs",
                        method,
                        url,
                        response.status_code,
                        delay,
                    )
                    await response.aclose()
                    await self._sleep(delay)
                    continue
                response.raise_for_status()
                return response
        raise AssertionError("unreachable")  # loop always returns or raises

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        """``GET url`` through :meth:`request`."""
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        """``POST url`` through :meth:`request` (not retried unless ``retry=True``)."""
        return await self.request("POST", url, **kwargs)

    async def get_json(self, url: str, **kwargs: Any) -> Any:
        """``GET url`` and return the decoded JSON body."""
        return (await self.request("GET", url, **kwargs)).json()

    async def post_json(self, url: str, **kwargs: Any) -> Any:
        """``POST url`` and return the decoded JSON body."""
        return (await self.request("POST", url, **kwargs)).json()


__all__ = [
    "DEFAULT_ACCEPT",
    "DEFAULT_ACCEPT_LANGUAGE",
    "DEFAULT_USER_AGENT",
    "IDEMPOTENT_METHODS",
    "RETRY_STATUSES",
    "PoliteClient",
    "parse_retry_after",
]
