"""``ProductSource`` implementation for Ozon.kz."""

from urllib.parse import quote

from market_sources.base import RawOffer, RawProduct, RawReview
from market_sources.ozon.browser import OzonBrowser
from market_sources.ozon.parse import parse_offer, parse_reviews, parse_search, parse_specs


def product_path(product_id: str) -> str:
    """Site path for a product. A bare sku resolves its slug server-side."""
    product = str(product_id).strip()
    if product.startswith("/product/"):
        return product if product.endswith("/") else f"{product}/"
    return f"/product/{product}/"


def search_path(query: str, budget: int | None = None) -> str:
    """Site path for a search query, price-capped at ``budget`` when given.

    Without the cap Ozon ranks by relevance and a cheap-budget query gets a
    first page priced entirely above it.
    """
    path = f"/search/?text={quote(query)}&from_global=true"
    if budget is not None:
        path += f"&currency_price={quote(f'0.000;{budget}.000')}"
    return path


class OzonSource:
    """Marketplace adapter for Ozon.kz, wiring ``OzonBrowser`` + ``parse``.

    Ozon.kz serves a single region (no Kaspi-style ``city`` param), so ``city``
    is accepted for interface parity but ignored.
    """

    name = "ozon"

    def __init__(self, browser: OzonBrowser | None = None, *, base_url: str | None = None) -> None:
        self._browser = browser or OzonBrowser(base_url=base_url)

    async def search(
        self, query: str, city: str | None = None, budget: int | None = None
    ) -> list[RawProduct]:
        """Search Ozon.kz for products matching ``query``, priced up to ``budget``."""
        payload = await self._browser.fetch_json(search_path(query, budget))
        return parse_search(payload)

    async def offers(self, product_id: str) -> list[RawOffer]:
        """Return the single PDP offer for ``product_id`` (Ozon has no seller list)."""
        payload = await self._browser.fetch_json(product_path(product_id))
        offer = parse_offer(payload, product_id)
        return [offer] if offer else []

    async def reviews(self, product_id: str) -> list[RawReview]:
        """Return user reviews for ``product_id``."""
        payload = await self._browser.fetch_json(f"{product_path(product_id)}reviews/")
        return parse_reviews(payload, product_id)

    async def specs(self, product_id: str) -> dict[str, str]:
        """Return raw specification key/value pairs for ``product_id``."""
        payload = await self._browser.fetch_json(product_path(product_id))
        return parse_specs(payload)

    async def aclose(self) -> None:
        """Tear down the underlying browser session."""
        await self._browser.aclose()


__all__ = ["OzonSource", "product_path", "search_path"]
