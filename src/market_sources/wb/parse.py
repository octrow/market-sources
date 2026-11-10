"""Wildberries search JSON -> ``RawProduct`` / ``RawOffer``.

The site's own search answer (``/exactmatch/.../search``) carries every product with its
sizes; a size's ``price.product`` is the price to pay in hundredths of the currency the
request asked for (``curr=kzt``), ``basic`` the price before discount. ``time1 + time2``
is the delivery time in hours.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from market_sources.base import RawOffer, RawProduct

SOURCE = "wb"
BASE_URL = "https://global.wildberries.ru"
DELIVERY_TIMEZONE = "Asia/Almaty"


def product_url(product_id: str) -> str:
    return f"{BASE_URL}/catalog/{product_id}/detail.aspx"


def currency_of(search_url: str) -> str:
    """The currency the search asked for (``curr=kzt`` -> ``KZT``); RUB when unsaid."""
    values = parse_qs(urlparse(search_url).query).get("curr") or ["rub"]
    return values[0].upper()


def _price(product: dict[str, Any]) -> float | None:
    """The price to pay, from the first size that has one; None when out of stock."""
    for size in product.get("sizes") or []:
        price = size.get("price") if isinstance(size, dict) else None
        if isinstance(price, dict) and isinstance(price.get("product"), int | float):
            if price["product"] > 0:
                return price["product"] / 100
    return None


def parse_search(payload: dict[str, Any], currency: str) -> list[RawProduct]:
    """Every product of a search answer that has an id and a name."""
    products = []
    for item in payload.get("products") or []:
        if not isinstance(item, dict) or not item.get("id") or not item.get("name"):
            continue
        product_id = str(item["id"])
        products.append(
            RawProduct(
                source=SOURCE,
                product_id=product_id,
                title=item["name"],
                price=_price(item),
                currency=currency,
                url=product_url(product_id),
                rating=item.get("reviewRating"),
                review_count=item.get("feedbacks"),
                brand=item.get("brand") or None,
            )
        )
    return products


def _delivery_date(item: dict[str, Any], now: datetime) -> str | None:
    hours = (item.get("time1") or 0) + (item.get("time2") or 0)
    if not hours:
        return None
    local = now.astimezone(ZoneInfo(DELIVERY_TIMEZONE))
    return (local + timedelta(hours=math.ceil(hours))).date().isoformat()


def parse_offer(
    item: dict[str, Any], currency: str, now: datetime | None = None
) -> RawOffer | None:
    """The one offer a search product carries: its seller, price and delivery day."""
    price = _price(item)
    if price is None:
        return None
    return RawOffer(
        source=SOURCE,
        product_id=str(item["id"]),
        seller=item.get("supplier") or None,
        price=price,
        currency=currency,
        merchant_id=str(item["supplierId"]) if item.get("supplierId") else None,
        merchant_rating=item.get("supplierRating"),
        delivery_date=_delivery_date(item, now or datetime.now(ZoneInfo(DELIVERY_TIMEZONE))),
    )
