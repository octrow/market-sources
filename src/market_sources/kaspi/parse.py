"""Pure functions turning raw Kaspi.kz JSON into ``market_sources.base`` models.

No I/O here — every function takes a plain ``dict`` (already ``.json()``-decoded)
and returns models or plain dicts. Keeps ``client.py`` (network) and this module
(shape) independently testable.
"""

import logging
import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from market_sources.base import RawOffer, RawProduct, RawReview, RawReviewStats

logger = logging.getLogger(__name__)

SOURCE = "kaspi"
BASE_URL = "https://kaspi.kz"
# Kaspi's delivery dates are shop-local; the consumer wants the calendar day there.
DELIVERY_TIMEZONE = "Asia/Almaty"
# Preference when the offer's ``deliveryType`` names no option it carries.
_DELIVERY_FALLBACK_ORDER = ("EXPRESS", "TO_DOOR")
# Review summary ``groupSummary`` ids that map onto ``RawReviewStats`` fields.
_GROUP_FIELDS = {"ALL": "total", "NEGATIVE": "negative_count", "PICTURE": "with_photos_count"}

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


def _card_images(card: dict) -> list[str]:
    """Every preview photo, best available size per entry, in payload order."""
    urls = []
    for image in card.get("previewImages") or []:
        url = image.get("medium") or image.get("large") or image.get("small")
        if url:
            urls.append(url)
    return urls


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
        images = _card_images(card)
        # The id offers/reviews/specs answer to; fashion variants need it.
        config_sku = str(card["configSku"]) if card.get("configSku") else None
        partner = card.get("isBrandOfficialPartner")
        category = card.get("categoryRu") or card.get("category")
        products.append(
            RawProduct(
                source=SOURCE,
                product_id=product_id,
                title=title,
                price=float(price) if price is not None else None,
                url=_card_link(card.get("shopLink")),
                image_url=images[0] if images else None,
                image_urls=images,
                rating=card.get("rating"),
                review_count=card.get("reviewsQuantity"),
                specs_available=bool(config_sku) or not _has_variant_letter_suffix(card),
                brand=card.get("brand") or None,
                review_id=config_sku if config_sku != product_id else None,
                official_partner=partner if isinstance(partner, bool) else None,
                category_path=[c for c in category if isinstance(c, str)]
                if isinstance(category, list)
                else [],
            )
        )
    return products


def parse_facets(payload: dict) -> dict[str, list[tuple[str, str]]]:
    """A search response's facets: title ("Высота") -> [(row id, row title)].

    A row id goes back into ``KaspiClient.search(filters=...)`` as it is.
    """
    facets: dict[str, list[tuple[str, str]]] = {}
    for facet in (payload.get("data") or {}).get("filters") or []:
        rows = [
            (row["id"], row["title"])
            for row in facet.get("rows") or []
            if isinstance(row.get("id"), str) and isinstance(row.get("title"), str)
        ]
        if isinstance(facet.get("title"), str) and rows:
            facets[facet["title"]] = rows
    return facets


def _delivery_option(offer: dict) -> dict | None:
    """The offer's delivery option matching its ``deliveryType``, else the fallback order."""
    options = offer.get("deliveryOptions")
    if not isinstance(options, dict) or not options:
        return None
    for key in (offer.get("deliveryType"), *_DELIVERY_FALLBACK_ORDER):
        if isinstance(options.get(key), dict):
            return options[key]
    first = next(iter(options.values()))
    return first if isinstance(first, dict) else None


def _delivery_date(raw: object) -> str | None:
    """ISO datetime -> the ``DELIVERY_TIMEZONE`` calendar date; naive values are UTC."""
    if not isinstance(raw, str):
        return None
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(ZoneInfo(DELIVERY_TIMEZONE)).date().isoformat()


def _delivery_cost(raw: object) -> float | None:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def parse_offers(payload: dict, product_id: str) -> list[RawOffer]:
    """Parse an offers (``offer-view/offers/{id}``) response into ``RawOffer``s."""
    offers = payload.get("offers") or []
    result = []
    for offer in offers:
        # A priceless offer would read as the cheapest seller at 0 KZT — drop it.
        if offer.get("price") is None:
            continue
        option = _delivery_option(offer) or {}
        result.append(
            RawOffer(
                source=SOURCE,
                product_id=str(product_id),
                seller=offer.get("merchantName"),
                price=float(offer["price"]),
                delivery=offer.get("deliveryDuration"),
                merchant_id=str(offer["merchantId"]) if offer.get("merchantId") else None,
                merchant_rating=offer.get("merchantRating"),
                merchant_review_count=offer.get("merchantReviewsQuantity"),
                delivery_date=_delivery_date(option.get("delivery")),
                delivery_cost=_delivery_cost(option.get("deliveryCost")),
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
                pros=comment.get("plus") or None,
                cons=comment.get("minus") or None,
            )
        )
    return result


def _whole(raw: object) -> int | None:
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else None


def _average(raw: object) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return float(raw)


def parse_review_stats(payload: dict, product_id: str) -> RawReviewStats | None:
    """Aggregate review numbers from a reviews response fetched with ``withAgg=true``.

    Returns ``None`` when the response has neither a ``summary`` nor a
    ``groupSummary``; malformed entries inside either are skipped.
    """
    summary = payload.get("summary")
    groups = payload.get("groupSummary")
    if not isinstance(summary, dict) and not isinstance(groups, list):
        return None
    summary = summary if isinstance(summary, dict) else {}
    groups = groups if isinstance(groups, list) else []
    statistic = summary.get("statistic") if isinstance(summary.get("statistic"), list) else []

    rating_counts: dict[int, int] = {}
    for entry in statistic:
        if not isinstance(entry, dict):
            continue
        rate, count = _whole(entry.get("rate")), _whole(entry.get("count"))
        if rate is not None and count is not None and 1 <= rate <= 5:
            rating_counts[rate] = count

    group_totals = {
        _GROUP_FIELDS[group["id"]]: _whole(group.get("total"))
        for group in groups
        if isinstance(group, dict) and group.get("id") in _GROUP_FIELDS
    }
    return RawReviewStats(
        source=SOURCE,
        product_id=str(product_id),
        rating_counts=rating_counts,
        average_rating=_average(summary.get("global")),
        **group_totals,
    )


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


__all__ = [
    "SOURCE",
    "parse_offers",
    "parse_review_stats",
    "parse_reviews",
    "parse_search",
    "parse_specs",
]
