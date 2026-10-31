"""``ProductSource`` implementation for Kaspi.kz."""

from market_sources.base import RawOffer, RawProduct, RawReview, RawReviewStats
from market_sources.cities import kaspi_city_id, resolve_city
from market_sources.kaspi.client import DEFAULT_CITY, KaspiClient
from market_sources.kaspi.parse import (
    parse_facets,
    parse_offers,
    parse_review_stats,
    parse_reviews,
    parse_search,
    parse_specs,
)

# ``filter="NEGATIVE"`` returns only the low-rated reviews; ask for more of them
# than the default page, since they are what a buyer reads first.
NEGATIVE_LIMIT = 30


class KaspiSource:
    """Marketplace adapter for Kaspi.kz, wiring ``KaspiClient`` + ``parse``.

    Fashion variants ("15358806b") answer offers/reviews/specs only under their
    ``configSku``. ``search`` remembers product_id -> configSku, so the other
    methods keep taking the numeric ``product_id`` callers already hold.

    ``city`` is a Kaspi cityId or a city name (``market_sources.cities``).
    Offers depend on the city, so ``offers`` asks the city ``search`` found the
    product in.
    """

    name = "kaspi"

    def __init__(self, client: KaspiClient | None = None, *, proxy: str | None = None) -> None:
        self._client = client or KaspiClient(proxy=proxy)
        self._city_clients: dict[str, KaspiClient] = {}
        self._api_ids: dict[str, str] = {}  # product_id -> configSku, from search
        self._product_cities: dict[str, str] = {}  # product_id -> cityId, from search
        self._facets: dict[tuple[str, str], dict[str, list[tuple[str, str]]]] = {}

    async def search(
        self,
        query: str,
        city: str | None = None,
        budget: int | None = None,
        *,
        page: int = 0,
        filters: str = "",
    ) -> list[RawProduct]:
        """Search Kaspi.kz for products matching ``query`` in ``city``.

        ``budget`` is a server-side price ceiling rounded up to Kaspi's price
        bucket (see ``kaspi.client.PRICE_BUCKETS``): filter exactly yourself.
        ``page`` counts from 0; Kaspi answers only pages 0 and 1 (12 cards each)
        without a login. ``filters``: facet row ids from ``facets``.
        """
        client = self._client_for_city(city)
        # Only pass what is set, so an injected client without these keeps working.
        extra: dict[str, object] = {"budget": budget} if budget is not None else {}
        if page:
            extra["page"] = page
        if filters:
            extra["filters"] = filters
        payload = await client.search(query, **extra)
        self._facets[(query, client.city)] = parse_facets(payload)
        products = parse_search(payload)
        for product in products:
            self._product_cities[product.product_id] = client.city
            if product.review_id and product.review_id != product.product_id:
                self._api_ids[product.product_id] = product.review_id
        return products

    def facets(self, query: str, city: str | None = None) -> dict[str, list[tuple[str, str]]]:
        """Facets of the last ``search`` for ``query`` in ``city``: title -> [(row id, title)]."""
        return self._facets.get((query, self._client_for_city(city).city), {})

    def _api_id(self, product_id: str, explicit: str | None = None) -> str:
        """The id Kaspi's per-product endpoints want: explicit, remembered, or as-is."""
        return explicit or self._api_ids.get(str(product_id), str(product_id))

    async def offers(self, product_id: str, *, city: str | None = None) -> list[RawOffer]:
        """Return seller offers for ``product_id`` in ``city``.

        Without ``city``, the city ``search`` found the product in, else the default.
        """
        client = self._client_for_city(city or self._product_cities.get(str(product_id)))
        payload = await client.offers(self._api_id(product_id))
        return parse_offers(payload, product_id)

    async def reviews(
        self, product_id: str, *, review_id: str | None = None, negative: bool = False
    ) -> list[RawReview]:
        """Return user reviews for ``product_id``; see :meth:`reviews_with_stats`."""
        reviews, _stats = await self.reviews_with_stats(
            product_id, review_id=review_id, negative=negative
        )
        return reviews

    async def reviews_with_stats(
        self, product_id: str, *, review_id: str | None = None, negative: bool = False
    ) -> tuple[list[RawReview], RawReviewStats | None]:
        """Return reviews and aggregate stats for ``product_id`` from one API call.

        ``review_id`` overrides the id sent to Kaspi (a card's ``configSku``);
        without it the one remembered from ``search`` is used. ``negative``
        fetches only low-rated reviews, up to ``NEGATIVE_LIMIT``.
        """
        api_id = self._api_id(product_id, review_id)
        if negative:
            payload = await self._client.reviews(api_id, limit=NEGATIVE_LIMIT, filter="NEGATIVE")
        else:
            payload = await self._client.reviews(api_id)
        return parse_reviews(payload, product_id), parse_review_stats(payload, product_id)

    async def specs(self, product_id: str) -> dict[str, str]:
        """Return raw specification key/value pairs for ``product_id``."""
        payload = await self._client.specs(self._api_id(product_id))
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
        city = resolve_city(city, kaspi_city_id)
        if city is None or city == self._client.city:
            return self._client
        if city not in self._city_clients:
            proxy = getattr(getattr(self._client, "http", None), "proxy", None)
            self._city_clients[city] = KaspiClient(city=city, proxy=proxy)
        return self._city_clients[city]


__all__ = ["DEFAULT_CITY", "NEGATIVE_LIMIT", "KaspiSource"]
