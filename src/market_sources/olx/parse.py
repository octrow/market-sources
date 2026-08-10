"""Pure functions turning raw OLX.kz JSON into ``market_sources.base`` models.

No I/O — every function takes a plain ``dict`` (already ``.json()``-decoded) and
returns models or plain dicts. On OLX a "product" *is* a single classified listing
(one offer per ad), and there are no product reviews. Condition (новый/б-у) and
seller type live in the offer's ``params``/``business`` fields; since ``RawProduct``
has no such fields they are surfaced via ``parse_specs`` under the ``"condition"``
and ``"seller_type"`` keys.
"""

from typing import Any

from market_sources.base import RawOffer, RawProduct

SOURCE = "olx"


def _param(offer: dict, key: str) -> dict[str, Any] | None:
    for param in offer.get("params") or []:
        if param.get("key") == key:
            value = param.get("value")
            return value if isinstance(value, dict) else None
    return None


def _price(offer: dict) -> int | None:
    price = _param(offer, "price")
    if not price:
        return None
    raw = price.get("value")
    return int(raw) if isinstance(raw, (int, float)) else None


def _image(offer: dict) -> str | None:
    photos = offer.get("photos") or []
    if not photos:
        return None
    first = photos[0]
    link = first.get("link")
    if not link:
        return None
    width = first.get("width") or 800
    height = first.get("height") or 600
    return link.replace("{width}", str(width)).replace("{height}", str(height))


def _city(offer: dict) -> str | None:
    # Top-level city/region are always null on OLX; real values live in `location`.
    city = (offer.get("location") or {}).get("city") or {}
    return city.get("name")


def _condition(offer: dict) -> str | None:
    state = _param(offer, "state")
    if not state:
        return None
    return state.get("label") or state.get("key")


def _seller_type(offer: dict) -> str:
    user = offer.get("user") or {}
    if user.get("seller_type"):
        return str(user["seller_type"])
    return "business" if offer.get("business") else "private"


def _offer_to_product(offer: dict) -> RawProduct | None:
    offer_id = offer.get("id")
    title = offer.get("title")
    if not offer_id or not title:
        return None
    return RawProduct(
        source=SOURCE,
        product_id=str(offer_id),
        title=title,
        price=_price(offer),
        url=offer.get("url"),
        image_url=_image(offer),
    )


def parse_search(payload: dict) -> list[RawProduct]:
    """Parse an ``/api/v1/offers/`` search response into ``RawProduct``s."""
    products = []
    for offer in payload.get("data") or []:
        product = _offer_to_product(offer)
        if product is not None:
            products.append(product)
    return products


def parse_offer(offer: dict) -> list[RawOffer]:
    """Turn a single OLX listing into a one-element ``RawOffer`` list.

    Each OLX ad is its own offer; the seller/price come straight from the ad.
    Returns ``[]`` if the listing has no price (e.g. "договорная"/service ads).
    """
    offer_id = offer.get("id")
    price = _price(offer)
    if not offer_id or price is None:
        return []
    user = offer.get("user") or {}
    return [
        RawOffer(
            source=SOURCE,
            product_id=str(offer_id),
            seller=user.get("name") or user.get("company_name"),
            price=float(price),
            city=_city(offer),
            merchant_id=str(user["id"]) if user.get("id") else None,
        )
    ]


def parse_specs(offer: dict) -> dict[str, str]:
    """Extract condition + seller type (+ other select params) as a flat dict."""
    specs: dict[str, str] = {}
    condition = _condition(offer)
    if condition:
        specs["condition"] = condition
    specs["seller_type"] = _seller_type(offer)
    city = _city(offer)
    if city:
        specs["city"] = city
    return specs


__all__ = ["SOURCE", "parse_offer", "parse_search", "parse_specs"]
