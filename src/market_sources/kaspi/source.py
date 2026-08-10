"""``ProductSource`` implementation for Kaspi.kz."""

from market_sources.base import RawOffer, RawProduct, RawReview
from market_sources.kaspi.client import DEFAULT_CITY, KaspiClient
from market_sources.kaspi.parse import parse_offers, parse_reviews, parse_search, parse_specs


class KaspiSource:
    """Marketplace adapter for Kaspi.kz, wiring ``KaspiClient`` + ``parse``."""

    name = "kaspi"

    def __init__(self, client: KaspiClient | None = None, *, proxy: str | None = None) -> None:
        self._client = client or KaspiClient(proxy=proxy)
        self._city_clients: dict[str, KaspiClient] = {}

    async def search(self, query: str, city: str | None = None) -> list[RawProduct]:
        """Search Kaspi.kz for products matching ``query`` in ``city``."""
        client = self._client_for_city(city)
        payload = await client.search(query)
        return parse_search(payload)

    async def offers(self, product_id: str) -> list[RawOffer]:
        """Return seller offers for ``product_id``."""
        payload = await self._client.offers(product_id)
        return parse_offers(payload, product_id)

    async def reviews(self, product_id: str) -> list[RawReview]:
        """Return user reviews for ``product_id``."""
        payload = await self._client.reviews(product_id)
        return parse_reviews(payload, product_id)

    async def specs(self, product_id: str) -> dict[str, str]:
        """Return raw specification key/value pairs for ``product_id``."""
        payload = await self._client.specs(product_id)
        return parse_specs(payload)

    async def aclose(self) -> None:
        """Close the default pool and every per-city pool this source spawned."""
        await self._client.aclose()
        for extra in self._city_clients.values():
            await extra.aclose()
        self._city_clients.clear()

    def _client_for_city(self, city: str | None) -> KaspiClient:
        """A client bound to ``city``, reusing the default one when it matches.

        Extra clients are cached per city (their pacing budget stays separate —
        the same trade-off the original made) and closed by :meth:`aclose`.
        """
        if city is None or city == self._client.city:
            return self._client
        if city not in self._city_clients:
            proxy = getattr(getattr(self._client, "http", None), "proxy", None)
            self._city_clients[city] = KaspiClient(city=city, proxy=proxy)
        return self._city_clients[city]


__all__ = ["DEFAULT_CITY", "KaspiSource"]
