"""Common product-source interface and raw Pydantic models.

Every marketplace adapter (Kaspi, Ozon, OLX) implements ``ProductSource``,
returning raw, source-shaped data. Normalization to canonical models is the
*consumer's* job — this library deliberately stops at the raw shapes.
"""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


class RawProduct(BaseModel):
    """A product hit as returned by a source's search endpoint."""

    source: str
    product_id: str
    title: str
    price: float | None = None
    currency: str = "KZT"
    url: str | None = None
    image_url: str | None = None
    # Every photo the listing carried, in the site's order (index 0 = lead photo,
    # so `image_url` is its mirror). Kept in full because a product is 1-6 views
    # of ONE item and a consumer learning visual taste needs all of them; the
    # sites already send the array, dropping it costs a re-fetch to recover.
    image_urls: list[str] = []
    rating: float | None = None
    review_count: int | None = None
    # False when the search card's id carried a non-numeric variant-letter
    # suffix (e.g. "16540138b") — a signal the card is a fashion/apparel
    # variant with no content-API specs page. Lets the consumer skip the specs
    # fetch entirely instead of paying for a guaranteed 404. Kaspi cards that
    # carry a `configSku` stay True: the source fetches specs by `review_id`.
    specs_available: bool = True
    # The brand exactly as the source names it, placeholders included (Kaspi
    # uses "Без бренда" for no-name goods). None = the source sent no brand.
    brand: str | None = None
    # The id the source's offers/reviews/specs endpoints want when it differs
    # from `product_id` (Kaspi: the card's `configSku`, which fashion variants
    # like "15358806b" need — the bare numeric id returns no reviews, no offers
    # and a 404 for specs). None = use `product_id`.
    review_id: str | None = None
    # True when the brand's official partner sells it. None = unknown.
    official_partner: bool | None = None
    # The source's category breadcrumb, root first, e.g.
    # ["Обувь", "Мужская обувь", "Мужские кроссовки и кеды"]. Empty = unknown.
    category_path: list[str] = []


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
    # ISO date (YYYY-MM-DD) the delivery lands on, in Asia/Almaty.
    delivery_date: str | None = None
    delivery_cost: float | None = None


class RawReview(BaseModel):
    """A single user review for a product."""

    source: str
    product_id: str
    rating: float | None = None
    author: str | None = None
    text: str = ""
    created_at: str | None = None
    # Unknown stays None: a missing field must never read as "no" or "zero".
    pros: str | None = None
    cons: str | None = None
    useful: int | None = None  # helpful votes
    purchased: bool | None = None  # verified purchase
    has_photos: bool | None = None


class RawReviewStats(BaseModel):
    """Aggregate review numbers for a product, as the source reports them."""

    source: str
    product_id: str
    rating_counts: dict[int, int] = Field(default_factory=dict)  # stars 1-5 -> count
    total: int | None = None
    negative_count: int | None = None
    with_photos_count: int | None = None
    average_rating: float | None = None


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
        """Return user reviews for ``product_id``.

        Adapters may add keyword-only options (Kaspi: ``negative``, ``review_id``);
        calling with ``product_id`` alone must always work.
        """
        ...

    async def specs(self, product_id: str) -> dict[str, str]:
        """Return raw specification key/value pairs for ``product_id``."""
        ...


__all__ = ["ProductSource", "RawOffer", "RawProduct", "RawReview", "RawReviewStats"]
