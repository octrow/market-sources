"""Pure functions turning raw Kaspi.kz JSON into ``market_sources.base`` models.

No I/O here — every function takes a plain ``dict`` (already ``.json()``-decoded)
and returns models or plain dicts. Keeps ``client.py`` (network) and this module
(shape) independently testable.
"""

import logging
import re

from market_sources.base import RawOffer, RawProduct, RawReview

logger = logging.getLogger(__name__)

SOURCE = "kaspi"
BASE_URL = "https://kaspi.kz"

# Fashion/apparel cards carry a variant-letter-suffixed id, e.g. "16540138b"
# (the URL slug repeats the same suffix: "/p/krossovki-seryi-16540138b/").
# Every downstream endpoint (offers/specs/reviews) only accepts the numeric
# part, so extract that instead of trusting the raw ``id`` field verbatim.
_LEADING_DIGITS_RE = re.compile(r"^(\d+)")
_SLUG_DIGITS_RE = re.compile(r"-(\d+)[a-zA-Z]*/?(?:\?|$)")


def _numeric_product_id(card: dict) -> str | None:
    """Extract a plain numeric product id from a search card.

    Prefers the card's ``id`` field (stripping any trailing variant letter);
    falls back to the numeric segment of the ``shopLink`` slug. Returns
    ``None`` if neither yields digits.
    """
    raw_id = card.get("id")
    if raw_id is not None:
        match = _LEADING_DIGITS_RE.match(str(raw_id))
        if match:
            return match.group(1)
    slug_match = _SLUG_DIGITS_RE.search(card.get("shopLink") or "")
    if slug_match:
        return slug_match.group(1)
    return None


def _has_variant_letter_suffix(card: dict) -> bool:
    """True when the card's raw ``id`` had a trailing variant letter stripped.

    Fashion/apparel cards carry ids like "16540138b"; the content (specs) API
    has no page for the variant id, only for the bare numeric product — so a
    stripped suffix is the signal that a specs fetch would be a guaranteed 404.
    """
    raw_id = card.get("id")
    if raw_id is None:
        return False
    raw_id = str(raw_id)
    match = _LEADING_DIGITS_RE.match(raw_id)
    return bool(match) and match.end() != len(raw_id)


def _card_image(card: dict) -> str | None:
    images = card.get("previewImages") or []
    if not images:
        return None
    first = images[0]
    return first.get("medium") or first.get("large") or first.get("small")


def _card_link(shop_link: str | None) -> str | None:
    if not shop_link:
        return None
    if shop_link.startswith("http"):
        return shop_link
    # API returns paths like "/p/<slug>-<id>/"; the public URL is under "/shop".
    if shop_link.startswith("/p/"):
        shop_link = "/shop" + shop_link
    return BASE_URL + shop_link


def parse_search(payload: dict) -> list[RawProduct]:
    """Parse a search (``product-view/pl/filters``) response into ``RawProduct``s."""
    cards = (payload.get("data") or {}).get("cards") or []
    products = []
    for card in cards:
        product_id = _numeric_product_id(card)
        title = card.get("title")
        if not product_id or not title:
            logger.debug(
                "kaspi: skipping card with no numeric id: raw_id=%r shop_link=%r",
                card.get("id"),
                card.get("shopLink"),
            )
            continue
        price = card.get("unitSalePrice") or card.get("unitPrice")
        products.append(
            RawProduct(
                source=SOURCE,
                product_id=product_id,
                title=title,
                price=float(price) if price is not None else None,
                url=_card_link(card.get("shopLink")),
                image_url=_card_image(card),
                rating=card.get("rating"),
                review_count=card.get("reviewsQuantity"),
                specs_available=not _has_variant_letter_suffix(card),
            )
        )
    return products


def _offer_price(offer: dict) -> float:
    price = offer.get("price")
    return float(price) if price is not None else 0.0


def parse_offers(payload: dict, product_id: str) -> list[RawOffer]:
    """Parse an offers (``offer-view/offers/{id}``) response into ``RawOffer``s."""
    offers = payload.get("offers") or []
    result = []
    for offer in offers:
        result.append(
            RawOffer(
                source=SOURCE,
                product_id=str(product_id),
                seller=offer.get("merchantName"),
                price=_offer_price(offer),
                delivery=offer.get("deliveryDuration"),
                merchant_id=str(offer["merchantId"]) if offer.get("merchantId") else None,
                merchant_rating=offer.get("merchantRating"),
                merchant_review_count=offer.get("merchantReviewsQuantity"),
            )
        )
    return result


def parse_reviews(payload: dict, product_id: str) -> list[RawReview]:
    """Parse a reviews (``review-view/api/v1/reviews/product/{id}``) response."""
    reviews = payload.get("data") or []
    result = []
    for review in reviews:
        comment = review.get("comment") or {}
        text = " ".join(
            part
            for part in (comment.get("plus"), comment.get("minus"), comment.get("text"))
            if part
        ).strip()
        result.append(
            RawReview(
                source=SOURCE,
                product_id=str(product_id),
                rating=review.get("rating"),
                author=review.get("author"),
                text=text,
                created_at=review.get("date"),
            )
        )
    return result


def _feature_value(feature: dict) -> str | None:
    values = [
        v.get("value") for v in (feature.get("featureValues") or []) if v.get("value") is not None
    ]
    if not values:
        return None
    return "; ".join(str(v) for v in values)


def parse_specs(payload: dict) -> dict[str, str]:
    """Parse a specs (``content/item/product/{id}``) response into a flat dict.

    Flattens all visible feature groups into ``{feature_name: value}``. Group
    names are dropped since ``ProductSource.specs`` returns a flat mapping.
    """
    specs: dict[str, str] = {}
    for group in payload.get("specifications") or []:
        for feature in group.get("features") or []:
            if feature.get("visible") is False:
                continue
            name = feature.get("name")
            value = _feature_value(feature)
            if not name or value is None:
                continue
            specs[name] = value
    return specs


__all__ = ["SOURCE", "parse_offers", "parse_reviews", "parse_search", "parse_specs"]
