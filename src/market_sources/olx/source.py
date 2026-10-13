"""``ProductSource`` implementation for OLX.kz (used/new classifieds)."""

import asyncio
import logging
from typing import Any

from market_sources.base import RawOffer, RawProduct, RawReview
from market_sources.olx.client import DEFAULT_CITY_ID, DEFAULT_LIMIT, OlxClient
from market_sources.olx.parse import parse_offer, parse_search, parse_specs

logger = logging.getLogger(__name__)

# We aim for ~50 listings; OLX caps a single page lower, so paginate once.
TARGET_RESULTS = 50
# OLX never shows more than 1000 results per search: total_elements stops at 1000
# and an offset past it is a 400 (checked 2026-10-13).
OLX_RESULT_CAP = 1000
# A narrower price range than this is not split further, even if it is still capped.
MIN_PRICE_SLICE_KZT = 1000


class OlxSource:
    """Marketplace adapter for OLX.kz, wiring ``OlxClient`` + ``parse``.

    OLX is a classifieds site: one ad == one offer, no product reviews. ``offers``
    returns the single listing price; ``reviews`` is always empty; ``specs`` carries
    condition (новый/б-у) and seller type.

    Search responses already carry each offer's full ``params`` (price, state,
    location), so we cache raw offers from every search and serve ``specs`` /
    ``offers`` from that cache — no per-listing detail HTTP call when the offer
    was already seen in a search (falls back to a detail fetch otherwise).
    """

    name = "olx"

    def __init__(self, client: OlxClient | None = None, *, proxy: str | None = None) -> None:
        self._client = client or OlxClient(proxy=proxy)
        self._offer_cache: dict[str, dict[str, Any]] = {}
        self._city_clients: dict[str, OlxClient] = {}

    def _cache_offers(self, payload: dict) -> None:
        for offer in payload.get("data") or []:
            offer_id = offer.get("id")
            if offer_id is not None:
                self._offer_cache[str(offer_id)] = offer

    async def search(
        self, query: str, city: str | None = None, budget: int | None = None
    ) -> list[RawProduct]:
        """Search OLX.kz for listings matching ``query`` (Almaty by default).

        Passes ``budget`` through as a server-side ``filter_float_price:to`` and
        paginates one extra page when OLX caps the first page below the target,
        so we approach ~50 listings when that many exist.
        """
        client = self._client_for_city(city)
        payload = await client.search(query, budget=budget)
        self._cache_offers(payload)
        products = parse_search(payload)
        data = payload.get("data") or []
        total = (payload.get("metadata") or {}).get("total_elements") or len(data)
        if data and len(products) < TARGET_RESULTS and len(data) < total:
            more = await client.search(query, offset=len(data), budget=budget)
            self._cache_offers(more)
            products.extend(parse_search(more))
        logger.info(
            "OLX fetched %d listings for %r (total available: %s)", len(products), query, total
        )
        return products

    async def search_all(
        self, query: str, min_price: int, max_price: int, city: str | None = None
    ) -> list[RawProduct]:
        """Every listing for ``query`` priced within [min_price, max_price].

        OLX caps each search at :data:`OLX_RESULT_CAP` results, so the price range is
        halved until each slice fits, then every page of every slice is fetched.
        Listings repeated across pages (promoted ads) are kept once.
        """
        client = self._client_for_city(city)
        slices = await self._price_slices(client, query, min_price, max_price)
        pages = [first for _, _, first in slices]
        pages += await asyncio.gather(
            *(
                client.search(query, offset=offset, budget=hi, min_price=lo)
                for lo, hi, first in slices
                for offset in range(DEFAULT_LIMIT, _visible_total(first), DEFAULT_LIMIT)
            )
        )
        products: dict[str, RawProduct] = {}
        for page in pages:
            self._cache_offers(page)
            for product in parse_search(page):
                products.setdefault(product.product_id, product)
        logger.info(
            "OLX fetched %d listings for %r in %d price slices (%d-%d)",
            len(products),
            query,
            len(slices),
            min_price,
            max_price,
        )
        return list(products.values())

    async def _price_slices(
        self, client: OlxClient, query: str, lo: int, hi: int
    ) -> list[tuple[int, int, dict]]:
        """Split [lo, hi] until each slice is under the cap; returns (lo, hi, first page)."""
        first = await client.search(query, budget=hi, min_price=lo)
        total = (first.get("metadata") or {}).get("total_elements") or 0
        if total < OLX_RESULT_CAP or hi - lo <= MIN_PRICE_SLICE_KZT:
            return [(lo, hi, first)]
        mid = (lo + hi) // 2
        logger.info("OLX %d-%d hits the %d-result cap, splitting at %d", lo, hi, total, mid)
        return await self._price_slices(client, query, lo, mid) + await self._price_slices(
            client, query, mid + 1, hi
        )

    async def offers(self, product_id: str) -> list[RawOffer]:
        """Return the single listing offer for ``product_id`` (empty if no price)."""
        offer = await self._offer(product_id)
        return parse_offer(offer)

    async def reviews(self, product_id: str) -> list[RawReview]:
        """OLX has no product reviews — always an empty list."""
        return []

    async def specs(self, product_id: str) -> dict[str, str]:
        """Return condition / seller_type / city for ``product_id``."""
        offer = await self._offer(product_id)
        return parse_specs(offer)

    async def aclose(self) -> None:
        """Close the default pool and every per-city pool this source spawned."""
        await self._client.aclose()
        for extra in self._city_clients.values():
            await extra.aclose()
        self._city_clients.clear()

    async def _offer(self, product_id: str) -> dict:
        """Cached raw offer for ``product_id``, fetching detail only on a miss."""
        cached = self._offer_cache.get(product_id)
        if cached is not None:
            return cached
        payload = await self._client.detail(product_id)
        offer = payload.get("data") or {}
        if offer:
            self._offer_cache[product_id] = offer
        return offer

    def _client_for_city(self, city: str | None) -> OlxClient:
        if city is None or city == self._client.city_id:
            return self._client
        if city not in self._city_clients:
            proxy = getattr(getattr(self._client, "http", None), "proxy", None)
            self._city_clients[city] = OlxClient(city_id=city, proxy=proxy)
        return self._city_clients[city]


def _visible_total(payload: dict) -> int:
    total = (payload.get("metadata") or {}).get("total_elements") or 0
    return min(total, OLX_RESULT_CAP)


__all__ = ["DEFAULT_CITY_ID", "OLX_RESULT_CAP", "TARGET_RESULTS", "OlxSource"]
