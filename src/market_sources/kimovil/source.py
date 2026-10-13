"""Look up a phone's Kimovil specs by a marketplace title."""

from __future__ import annotations

import logging

import httpx

from market_sources.kimovil.client import KimovilClient
from market_sources.kimovil.parse import (
    KimovilHit,
    KimovilPhone,
    best_match,
    model_query,
    parse_autocomplete,
    parse_phone,
)

logger = logging.getLogger(__name__)

MAX_SEARCHES = 5  # autocomplete calls per lookup
_MIN_QUERY_WORDS = 3  # trimming stops before the query is a brand plus one word


def _search_queries(query: str) -> list[str]:
    """What to send to Kimovil's search, most specific first.

    The full query; then without its first word, because Kimovil names some sub-brands
    bare ("POCO X6 Pro") while titles prefix the parent ("Xiaomi Poco X6 Pro"); then the
    full query trimmed from the end, because leftover words ("titanium gray") make the
    search return nothing.
    """
    words = query.split()
    queries = [query, " ".join(words[1:])]
    queries += [" ".join(words[:n]) for n in range(len(words) - 1, _MIN_QUERY_WORDS - 1, -1)]
    unique = [q for i, q in enumerate(queries) if q and q not in queries[:i]]
    return unique[:MAX_SEARCHES]


class KimovilSource:
    """Title -> :class:`KimovilPhone` via Kimovil's autocomplete, then the specs page.

    Not a ``ProductSource``: Kimovil sells nothing, it is a spec reference. Every miss
    (no confident match, an HTTP error) is ``None``; the caller caches as it sees fit.
    """

    name = "kimovil"

    def __init__(self, client: KimovilClient | None = None, *, proxy: str | None = None) -> None:
        self._client = client or KimovilClient(proxy=proxy)

    async def lookup(self, title: str) -> KimovilPhone | None:
        query = model_query(title)
        try:
            hit = await self._resolve(query)
            if hit is None:
                logger.info("Kimovil: no confident match for %r", query)
                return None
            return parse_phone(await self._client.page(hit.slug), hit)
        except httpx.HTTPError as exc:
            logger.warning("Kimovil lookup failed for %r: %s", query, exc)
            return None

    async def _resolve(self, query: str) -> KimovilHit | None:
        """Search :func:`_search_queries` in order until one yields a confident match."""
        for search in _search_queries(query):
            hits = parse_autocomplete(await self._client.autocomplete(search))
            if hit := best_match(query, hits):
                return hit
        return None

    async def aclose(self) -> None:
        await self._client.aclose()


__all__ = ["KimovilSource"]
