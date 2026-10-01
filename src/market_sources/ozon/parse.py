"""Pure parsers over Ozon composer-api JSON — no browser, no network.

Ported from ``ozon-mcp-server/src/parse.js`` (MIT) via buy-smarter. Every
function takes an already-decoded composer-api page ``dict`` and returns
``market_sources.base`` models, so it is unit-testable against saved samples.
Widget lookups match on the widget NAME (the part before the first "-" in a
``widgetStates`` key), so ``webPrice`` never matches ``webPriceDecreasedCompact``.
"""

import json
import logging
import re
from datetime import UTC, datetime

from market_sources.base import RawOffer, RawProduct, RawReview

log = logging.getLogger(__name__)

SOURCE = "ozon"
BASE_URL = "https://www.ozon.kz"

# ozon.kz search tiles carry two priceV2 blocks: an installment price
# ("1 216 ₸ × 12 мес") and the real price. Skip installment texts.
_INSTALLMENT_RE = re.compile(r"мес|×|/")
_BADGE_RE = re.compile(
    r"^(стало дешевле|оригинал|хит|новинка|акция|распродажа|выбор|"
    r"бестселлер|ozon|premium|самовывоз|скидка)",
    re.I,
)


def _widget(page: dict, name: str) -> dict | None:
    ws = page.get("widgetStates") or {}
    key = next((k for k in ws if k.split("-", 1)[0] == name), None)
    if key is None:
        return None
    try:
        return json.loads(ws[key])
    except (json.JSONDecodeError, TypeError):
        log.debug("ozon widget %s: unparseable widgetStates JSON", name, exc_info=True)
        return None


def _widgets(page: dict, name: str) -> list[dict]:
    ws = page.get("widgetStates") or {}
    result = []
    for key, value in ws.items():
        if key.split("-", 1)[0] != name:
            continue
        try:
            result.append(json.loads(value))
        except (json.JSONDecodeError, TypeError):
            log.debug("ozon widget %s: skipping unparseable widgetStates JSON", name, exc_info=True)
            continue
    return result


def price_to_int(text: str | None) -> int | None:
    """``"14 589 ₸"`` -> ``14589``; ``None``/garbage -> ``None``."""
    if not isinstance(text, str):
        return None
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def _clean_url(link: str | None) -> str | None:
    if not link:
        return None
    path = str(link).split("?")[0]
    return path if path.startswith("http") else f"{BASE_URL}{path}"


def _rs_text(nodes: list | None) -> str:
    parts = [n.get("text") or n.get("content") for n in (nodes or []) if isinstance(n, dict)]
    return re.sub(r"\s+", " ", " ".join(p for p in parts if p)).strip()


def _search_price(main_state: list) -> tuple[int | None, int | None]:
    for block in main_state:
        if block.get("type") != "priceV2":
            continue
        prices = (block.get("priceV2") or {}).get("price") or []
        price_text = next((p.get("text") for p in prices if p.get("textStyle") == "PRICE"), None)
        if not price_text or _INSTALLMENT_RE.search(price_text):
            continue
        old_text = next(
            (p.get("text") for p in prices if p.get("textStyle") == "ORIGINAL_PRICE"), None
        )
        return price_to_int(price_text), price_to_int(old_text)
    return None, None


def _search_rating(main_state: list) -> tuple[float | None, int | None]:
    for block in main_state:
        labels = block.get("labelListV2")
        if not labels or "ic_s_star" not in json.dumps(labels):
            continue
        texts = [i.get("text", {}).get("text") for i in labels.get("items", []) if i.get("text")]
        rating = float(texts[0].replace(",", ".")) if texts and texts[0] else None
        reviews = price_to_int(texts[1]) if len(texts) > 1 else None
        return rating, reviews
    return None, None


def _search_images(item: dict) -> list[str]:
    """Every tile photo, in payload order, falling back to the cover image.

    Search tiles usually carry one entry; the gallery lives on the product page.
    The list shape is what the protocol promises, so a single-photo tile is a
    one-element list rather than a special case for the consumer to handle.
    """
    tile = item.get("tileImage") or {}
    urls = [
        link
        for entry in tile.get("items") or []
        if (link := (entry.get("image") or {}).get("link"))
    ]
    if urls:
        return urls
    cover = tile.get("coverImage")
    return [cover] if cover else []


def _parse_search_item(item: dict) -> RawProduct | None:
    main_state = item.get("mainState") or []
    name = next(
        (s.get("textDS", {}).get("text") for s in main_state if s.get("id") == "name"), None
    )
    price, _old = _search_price(main_state)
    sku = item.get("sku") or item.get("id")
    if not sku or price is None or not name:
        return None
    rating, reviews = _search_rating(main_state)
    images = _search_images(item)
    return RawProduct(
        source=SOURCE,
        product_id=str(sku),
        title=name,
        price=float(price),
        url=_clean_url((item.get("action") or {}).get("link")),
        image_url=images[0] if images else None,
        image_urls=images,
        rating=rating,
        review_count=reviews,
    )


def parse_search(page: dict) -> list[RawProduct]:
    """Parse the ``tileGridDesktop`` widget into ``RawProduct``s."""
    grid = _widget(page, "tileGridDesktop") or {}
    return [p for item in (grid.get("items") or []) if (p := _parse_search_item(item))]


def _parse_seller(page: dict) -> dict | None:
    widget = _widget(page, "webCurrentSeller")
    if not widget:
        return None
    cell = widget.get("sellerCell") or {}
    name = (cell.get("centerBlock") or {}).get("title", {}).get("text")
    if not name and isinstance(widget.get("title"), dict):
        name = widget["title"].get("text")
    if not name:
        return None
    rating_text = (widget.get("rating") or {}).get("title", {}).get("text")
    rating = float(rating_text.replace(",", ".")) if rating_text else None
    return {"name": name, "rating": rating}


def parse_offer(page: dict, product_id: str) -> RawOffer | None:
    """Build a single ``RawOffer`` from ``webPrice`` + ``webCurrentSeller``.

    Ozon has no Kaspi-style multi-seller list on the PDP, so a product maps to
    exactly one offer (the buy-box price + current seller).
    """
    price_widget = _widget(page, "webPrice")
    if not price_widget:
        return None
    price = price_to_int(price_widget.get("cardPrice")) or price_to_int(price_widget.get("price"))
    if price is None:
        return None
    seller = _parse_seller(page)
    return RawOffer(
        source=SOURCE,
        product_id=str(product_id),
        seller=seller["name"] if seller else None,
        price=float(price),
        in_stock=bool(price_widget.get("isAvailable", True)),
        merchant_rating=seller["rating"] if seller else None,
    )


def parse_specs(page: dict) -> dict[str, str]:
    """Parse ``webShortCharacteristics`` into a flat ``{name: value}`` dict."""
    widget = _widget(page, "webShortCharacteristics") or {}
    specs: dict[str, str] = {}
    for char in widget.get("characteristics") or []:
        title = _rs_text((char.get("title") or {}).get("textRs"))
        value = _rs_text(char.get("values") or char.get("valueRs"))
        value = value.rstrip(", ").strip()
        if title and value:
            specs[title] = value
    return specs


def _unix_to_date(ts: int | None) -> str | None:
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=UTC).date().isoformat()
    except (ValueError, OSError, OverflowError):
        return None


def _review_author(review: dict) -> str | None:
    author = review.get("author") or {}
    name = " ".join(p for p in (author.get("firstName"), author.get("lastName")) if p).strip()
    if name:
        return name
    return "Аноним" if review.get("isAnonymous") else None


def _review_text(content: dict) -> str:
    parts = (content.get("positive"), content.get("negative"), content.get("comment"))
    return " ".join(p for p in parts if p).strip()


def parse_reviews(page: dict, product_id: str) -> list[RawReview]:
    """Parse the ``webListReviews`` widget into ``RawReview``s."""
    widget = _widget(page, "webListReviews") or {}
    raw = widget.get("reviews") or widget.get("items") or []
    result = []
    for review in raw:
        content = review.get("content") or {}
        score = content.get("score")
        result.append(
            RawReview(
                source=SOURCE,
                product_id=str(product_id),
                rating=float(score) if isinstance(score, (int, float)) else None,
                author=_review_author(review),
                text=_review_text(content),
                created_at=_unix_to_date(review.get("publishedAt") or review.get("createdAt")),
            )
        )
    return result


__all__ = [
    "SOURCE",
    "parse_offer",
    "parse_reviews",
    "parse_search",
    "parse_specs",
    "price_to_int",
]
