"""``ProductSource`` implementation for Wildberries (KZ prices through the user's Chrome)."""

from __future__ import annotations

from typing import Any, Protocol

from market_sources.base import RawOffer, RawProduct, RawReview
from market_sources.lru import LRUDict
from market_sources.wb.browser import WbBrowser
from market_sources.wb.parse import SOURCE, currency_of, parse_offer, parse_search

_ITEMS_KEPT = 500


class WbBrowserLike(Protocol):
    async def search_json(self, query: str) -> tuple[str, dict]: ...

    async def aclose(self) -> None: ...


class WbSource:
    """Wildberries search; ``offers`` answers from the search that found the product.

    The city follows the region set in the user's browser, so ``city`` is accepted for
    interface parity and ignored. Reviews and specs are not read yet.
    """

    name = SOURCE

    def __init__(self, browser: WbBrowserLike | None = None) -> None:
        self._browser: WbBrowserLike = browser or WbBrowser()
        self._items: LRUDict[str, tuple[dict[str, Any], str]] = LRUDict(_ITEMS_KEPT)

    async def search(self, query: str, city: str | None = None) -> list[RawProduct]:
        url, payload = await self._browser.search_json(query)
        currency = currency_of(url)
        for item in payload.get("products") or []:
            if isinstance(item, dict) and item.get("id"):
                self._items[str(item["id"])] = (item, currency)
        return parse_search(payload, currency)

    async def offers(self, product_id: str) -> list[RawOffer]:
        kept = self._items.get(str(product_id))
        offer = parse_offer(*kept) if kept else None
        return [offer] if offer else []

    async def reviews(self, product_id: str) -> list[RawReview]:
        return []  # ponytail: feedbacks2.wb.ru when WB reviews start to matter

    async def specs(self, product_id: str) -> dict[str, str]:
        return {}

    async def aclose(self) -> None:
        await self._browser.aclose()
