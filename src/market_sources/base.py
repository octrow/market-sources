"""Common product-source interface and raw Pydantic models.

Every marketplace adapter (Kaspi, Ozon, OLX) implements ``ProductSource``,
returning raw, source-shaped data. Normalization to canonical models is the
*consumer's* job — this library deliberately stops at the raw shapes.
"""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class RawProduct(BaseModel):
    """A product hit as returned by a source's search endpoint."""

    source: str
    product_id: str
    title: str
    price: float | None = None
    currency: str = "KZT"
    url: str | None = None
    image_url: str | None = None
    rating: float | None = None
    review_count: int | None = None
    # False when the search card's id carried a non-numeric variant-letter
    # suffix (e.g. "16540138b") — a signal the card is a fashion/apparel
    # variant with no content-API specs page. Lets the consumer skip the specs
    # fetch entirely instead of paying for a guaranteed 404.
    specs_available: bool = True


class RawOffer(BaseModel):
    """A single seller offer for a product."""

    source: str
    product_id: str
    seller: str | None = None
    price: float
    currency: str = "KZT"
    city: str | None = None
    delivery: str | None = None
    in_stock: bool = True
    merchant_id: str | None = None
    merchant_rating: float | None = None
    merchant_review_count: int | None = None


class RawReview(BaseModel):
    """A single user review for a product."""

    source: str
    product_id: str
    rating: float | None = None
    author: str | None = None
    text: str = ""
    created_at: str | None = None


@runtime_checkable
class ProductSource(Protocol):
    """Interface every marketplace adapter must satisfy."""

    name: str

    async def search(self, query: str, city: str | None = None) -> list[RawProduct]:
        """Search the source for products matching ``query`` in ``city``."""
        ...

    async def offers(self, product_id: str) -> list[RawOffer]:
        """Return seller offers for ``product_id``."""
        ...

    async def reviews(self, product_id: str) -> list[RawReview]:
        """Return user reviews for ``product_id``."""
        ...

    async def specs(self, product_id: str) -> dict[str, str]:
        """Return raw specification key/value pairs for ``product_id``."""
        ...


__all__ = ["ProductSource", "RawOffer", "RawProduct", "RawReview"]
