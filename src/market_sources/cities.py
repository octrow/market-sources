"""City name -> the id each site wants: Kaspi ``cityId``, OLX ``city_id``.

Every id below was checked live on 2026-10-22: Kaspi's product page titles itself
"... в Шымкенте" for ``c=511010000``, and OLX's location autocomplete returns the
id. OLX lists two Актау; 5633 (Мангистауская область) is the city.
"""

from __future__ import annotations

from collections.abc import Callable

# (Russian name, Latin name, Kaspi cityId, OLX city_id)
_CITIES = (
    ("алматы", "almaty", "750000000", "1"),
    ("астана", "astana", "710000000", "87"),
    ("шымкент", "shymkent", "511010000", "47"),
    ("караганда", "karaganda", "351010000", "39"),
    ("актобе", "aktobe", "151010000", "26"),
    ("тараз", "taraz", "311010000", "53"),
    ("павлодар", "pavlodar", "551010000", "36"),
    ("усть-каменогорск", "ust-kamenogorsk", "631010000", "52"),
    ("семей", "semey", "632810000", "58"),
    ("атырау", "atyrau", "231010000", "28"),
    ("костанай", "kostanay", "391010000", "51"),
    ("кызылорда", "kyzylorda", "431010000", "45"),
    ("уральск", "uralsk", "271010000", "19"),
    ("петропавловск", "petropavlovsk", "591010000", "7"),
    ("актау", "aktau", "471010000", "5633"),
    ("туркестан", "turkestan", "512610000", "74"),
    ("кокшетау", "kokshetau", "111010000", "24"),
    ("талдыкорган", "taldykorgan", "191010000", "40"),
    ("экибастуз", "ekibastuz", "552210000", "85"),
    ("темиртау", "temirtau", "352410000", "65"),
    ("балхаш", "balkhash", "351610000", "72"),
)
_ALIASES = {"нур-султан": "астана", "nur-sultan": "астана"}

KASPI_CITY_IDS = {name: kaspi for ru, lat, kaspi, _ in _CITIES for name in (ru, lat)}
OLX_CITY_IDS = {name: olx for ru, lat, _, olx in _CITIES for name in (ru, lat)}


def _key(name: str | None) -> str:
    key = (name or "").strip().lower()
    return _ALIASES.get(key, key)


def kaspi_city_id(name: str | None) -> str | None:
    """Kaspi ``cityId`` for a city name (Russian or Latin, any case), else None."""
    return KASPI_CITY_IDS.get(_key(name))


def olx_city_id(name: str | None) -> str | None:
    """OLX.kz ``city_id`` for a city name (Russian or Latin, any case), else None."""
    return OLX_CITY_IDS.get(_key(name))


def resolve_city(city: str | None, lookup: Callable[[str | None], str | None]) -> str | None:
    """A site id as-is, a known name as its id, None for no city; else ValueError.

    An unknown name would otherwise go out as an id: a garbled search on OLX and
    a header encoding error on Kaspi.
    """
    if not city or city.isdigit():
        return city or None
    resolved = lookup(city)
    if resolved is None:
        raise ValueError(f"unknown city {city!r}: pass the site's city id")
    return resolved


__all__ = ["KASPI_CITY_IDS", "OLX_CITY_IDS", "kaspi_city_id", "olx_city_id", "resolve_city"]
