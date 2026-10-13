"""Pure functions turning Kimovil JSON/HTML into models. No I/O.

``parse_phone`` needs the ``kimovil`` extra (selectolax); it is imported lazily so the
rest of the package imports without it.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

from market_sources.kimovil.client import page_url

SMARTPHONES = "smartphones"
# Network suffixes Kimovil appends to a name ("POCO X6 Pro 5G") that titles often omit.
_OPTIONAL_TOKENS = frozenset({"5g", "4g", "lte"})
_MIN_MATCHED_TOKENS = 2  # a brand alone is not a model
_TOKEN = re.compile(r"[a-z0-9]+\+?")  # "pro+" is a model of its own, not "pro"
_MEMORY = re.compile(
    r"\d+\s*(?:gb|гб|tb|тб)?\s*/\s*\d+\s*(?:gb|гб|tb|тб)?|\d+\s*(?:gb|гб|tb|тб)\b", re.IGNORECASE
)
_BRACKETED = re.compile(r"\([^)]*\)")
_CYRILLIC_WORD = re.compile(r"\b\w*[а-яё]\w*\b", re.IGNORECASE)
_BATTERY_LABEL = "Battery capacity"  # "Capacity" in mAh, split from the storage row


class KimovilHit(BaseModel):
    """One autocomplete result: a model and its Kimovil index (0-100)."""

    full_name: str
    slug: str
    ki: float | None = None
    usd: float | None = None


class KimovilPhone(BaseModel):
    """A model's specs page: table rows keyed by Kimovil's English labels."""

    full_name: str
    slug: str
    url: str
    ki: float | None = None
    specs: dict[str, str] = {}


def model_query(title: str) -> str:
    """A marketplace title cut down to brand + model words for Kimovil's search.

    Drops memory ("8/256Gb", "128 ГБ"), bracketed notes and every Cyrillic word
    ("Смартфон", colours), which leaves the Latin brand and model.
    """
    text = _CYRILLIC_WORD.sub(" ", _BRACKETED.sub(" ", _MEMORY.sub(" ", title)))
    return " ".join(_TOKEN.findall(text.lower()))


def parse_autocomplete(payload: dict) -> list[KimovilHit]:
    """Smartphone hits from the autocomplete JSON; rumours and other devices dropped."""
    hits = []
    for result in payload.get("results") or []:
        if result.get("is_rumor") or result.get("result_type", SMARTPHONES) != SMARTPHONES:
            continue
        if not result.get("full_name") or not result.get("url"):
            continue
        hits.append(
            KimovilHit(
                full_name=result["full_name"],
                slug=result["url"],
                ki=result.get("ki"),
                usd=result.get("usd"),
            )
        )
    return hits


def best_match(query: str, hits: list[KimovilHit]) -> KimovilHit | None:
    """The hit whose every name word is in ``query``, the most specific one first.

    Kimovil's own order is not exact-first ("iphone 13" lists the Pro Max on top), so a
    hit qualifies only when its name names nothing the query does not: "Apple iPhone 13
    Pro" is rejected for "apple iphone 13". The one exception is the name's first word,
    the parent brand. Among the rest, the one matching the most words wins, then the
    shortest name.
    """
    words = set(_TOKEN.findall(query.lower()))
    best: tuple[tuple[int, int], KimovilHit] | None = None
    for hit in hits:
        name = _TOKEN.findall(hit.full_name.lower())
        required = set(name) - _OPTIONAL_TOKENS
        # Kimovil prefixes the parent brand ("Xiaomi Redmi Note 12 Pro"); titles often don't.
        unmatched = required - words
        if unmatched and unmatched != {name[0]}:
            continue
        matched = len(required - unmatched)
        if matched < _MIN_MATCHED_TOKENS:
            continue
        rank = (matched, -len(name))
        if best is None or rank > best[0]:
            best = (rank, hit)
    return best[1] if best else None


def parse_phone(html: str, hit: KimovilHit) -> KimovilPhone:
    """The specs page as label -> value; the first row wins when a label repeats.

    Labels repeat across sections ("Type", "Resolution", "Capacity"); the page lists
    the screen and storage before the camera and battery rows that reuse them. A
    "Capacity" in mAh is the battery and is kept under ``"Battery capacity"``.
    """
    from selectolax.lexbor import LexborHTMLParser

    tree = LexborHTMLParser(html)
    specs: dict[str, str] = {}
    for label, value in _rows(tree):
        if label == "Capacity" and "mah" in value.lower():
            label = _BATTERY_LABEL
        specs.setdefault(label, value)
    dxomark = tree.css_first("dl.dxomark-scores-dl dd")
    if dxomark and (score := re.match(r"\s*(\d+)", dxomark.text())):
        specs["DxOMark"] = score.group(1)
    return KimovilPhone(
        full_name=hit.full_name, slug=hit.slug, url=page_url(hit.slug), ki=hit.ki, specs=specs
    )


def _rows(tree: Any) -> list[tuple[str, str]]:
    rows = []
    for row in tree.css("table tr"):
        cells = row.css("td, th")
        if len(cells) < 2:  # noqa: PLR2004 - a label cell and a value cell
            continue
        label, value = _squash(cells[0].text()), _squash(cells[1].text())
        if label and value and label != value:
            rows.append((label, value))
    return rows


def _squash(text: str) -> str:
    return " ".join(text.split())


__all__ = [
    "KimovilHit",
    "KimovilPhone",
    "best_match",
    "model_query",
    "parse_autocomplete",
    "parse_phone",
]
