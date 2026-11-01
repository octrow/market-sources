"""``ProductSource`` implementation for OLX.kz (used/new classifieds)."""

import asyncio
import logging
from typing import Any, Protocol

from market_sources.base import RawOffer, RawProduct, RawReview
from market_sources.cities import olx_city_id, olx_region_id, resolve_city
from market_sources.olx.client import DEFAULT_CITY_ID, DEFAULT_LIMIT, PRICE_ASC, OlxClient
from market_sources.olx.parse import parse_offer, parse_search, parse_specs

logger = logging.getLogger(__name__)

# We aim for ~50 listings; OLX caps a single page lower, so paginate once.
TARGET_RESULTS = 50
# OLX never shows more than 1000 results per search: total_elements stops at 1000
# and an offset past it is a 400 (checked 2026-10-13).
OLX_RESULT_CAP = 1000
# A narrower price range than this is not split further, even if it is still capped.
MIN_PRICE_SLICE_KZT = 1000


class OlxClientLike(Protocol):
    """What ``OlxSource`` calls on its client: ``OlxClient``, or a stand-in."""

    city_id: str

    async def search(
        self,
        query: str,
        *,
        offset: int = 0,
        budget: int | None = None,
        min_price: int | None = None,
        sort_by: str | None = None,
        category_id: str | None = None,
    ) -> Any: ...

    async def detail(self, offer_id: str) -> Any: ...

    async def aclose(self) -> None: ...


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

    def __init__(self, client: OlxClientLike | None = None, *, proxy: str | None = None) -> None:
        self._client: OlxClientLike = client or OlxClient(proxy=proxy)
        self._offer_cache: dict[str, dict[str, Any]] = {}
        self._city_clients: dict[str, OlxClientLike] = {}

    def _cache_offers(self, payload: dict) -> None:
        for offer in payload.get("data") or []:
            offer_id = offer.get("id")
            if offer_id is not None:
                self._offer_cache[str(offer_id)] = offer

    async def search(
        self,
        query: str,
        city: str | None = None,
        budget: int | None = None,
        *,
        region: bool = False,
    ) -> list[RawProduct]:
        """Search OLX.kz for listings matching ``query`` (Almaty by default).

        ``region``: the city's whole oblast (``olx_region_id``), villages around the
        city too; a city without a known region searches the city alone.

        Passes ``budget`` through as a server-side ``filter_float_price:to`` and
        paginates one extra page when OLX caps the first page below the target,
        so we approach ~50 listings when that many exist.
        """
        client = self._client_for_region(city) if region else self._client_for_city(city)
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

    async def search_cheapest(
        self,
        query: str,
        city: str | None = None,
        budget: int | None = None,
        *,
        region: bool = False,
        category_id: str | None = None,
        limit: int = 200,
    ) -> list[RawProduct]:
        """Up to ``limit`` priced listings for ``query``, cheapest first, page by page.

        A broad query ("посудомоечная машина": 500+ ads around Алматы) sorted by
        relevance shows only its first ~50; cheapest first reaches the bargains a
        relevance page leaves out. Without ``category_id`` the cheap end is houses and
        services the fuzzy search matched: pass the category (``category_of``).
        Free listings (price 0) are skipped.
        """
        client = self._client_for_region(city) if region else self._client_for_city(city)
        products: dict[str, RawProduct] = {}
        for offset in range(0, min(limit, OLX_RESULT_CAP), DEFAULT_LIMIT):
            page = await client.search(
                query,
                offset=offset,
                budget=budget,
                min_price=1,
                sort_by=PRICE_ASC,
                category_id=category_id,
            )
            self._cache_offers(page)
            for product in parse_search(page):
                products.setdefault(product.product_id, product)
            if len(page.get("data") or []) < DEFAULT_LIMIT // 2:
                break
        logger.info("OLX fetched %d cheapest listings for %r", len(products), query)
        return list(products.values())[:limit]

    def category_of(self, product_id: str) -> str | None:
        """The OLX category id of a listing a search returned, else None."""
        category = (self._offer_cache.get(str(product_id)) or {}).get("category") or {}
        return str(category["id"]) if category.get("id") is not None else None

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
        self, client: OlxClientLike, query: str, lo: int, hi: int
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

    def _client_for_region(self, city: str | None) -> OlxClientLike:
        """A client for the region of ``city`` (default: the default client's city)."""
        region = olx_region_id(resolve_city(city, olx_city_id) or self._client.city_id)
        if region is None:
            return self._client_for_city(city)
        key = f"region:{region}"
        if key not in self._city_clients:
            proxy = getattr(getattr(self._client, "http", None), "proxy", None)
            self._city_clients[key] = OlxClient(region_id=region, proxy=proxy)
        return self._city_clients[key]

    def _client_for_city(self, city: str | None) -> OlxClientLike:
        """A client for ``city`` (an OLX city_id or a city name), cached per city."""
        city = resolve_city(city, olx_city_id)
        if city is None or city == self._client.city_id:
            return self._client
        if city not in self._city_clients:
            proxy = getattr(getattr(self._client, "http", None), "proxy", None)
            self._city_clients[city] = OlxClient(city_id=city, proxy=proxy)
        return self._city_clients[city]


def _visible_total(payload: dict) -> int:
    total = (payload.get("metadata") or {}).get("total_elements") or 0
    return min(total, OLX_RESULT_CAP)


__all__ = ["DEFAULT_CITY_ID", "OLX_RESULT_CAP", "TARGET_RESULTS", "OlxClientLike", "OlxSource"]
