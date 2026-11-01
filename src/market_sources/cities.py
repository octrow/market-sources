"""City name -> the id each site wants: Kaspi ``cityId``, OLX ``city_id``.

Every id below was checked live on 2026-10-22: Kaspi's product page titles itself
"... в Шымкенте" for ``c=511010000``, and OLX's location autocomplete returns the
id. OLX lists two Актау; 5633 (Мангистауская область) is the city.
"""

from __future__ import annotations

from collections.abc import Callable

# (Russian name, Latin name, Kaspi cityId, OLX city_id, OLX region_id)
# OLX regions are the old oblasts (checked live 2026-11-01 from each city's ads):
# Семей is in ВКО (14), Талдыкорган in Алматинская (8), as OLX has them.
_CITIES = (
    ("алматы", "almaty", "750000000", "1", "8"),
    ("астана", "astana", "710000000", "87", "13"),
    ("шымкент", "shymkent", "511010000", "47", "6"),
    ("караганда", "karaganda", "351010000", "39", "5"),
    ("актобе", "aktobe", "151010000", "26", "2"),
    ("тараз", "taraz", "311010000", "53", "10"),
    ("павлодар", "pavlodar", "551010000", "36", "1"),
    ("усть-каменогорск", "ust-kamenogorsk", "631010000", "52", "14"),
    ("семей", "semey", "632810000", "58", "14"),
    ("атырау", "atyrau", "231010000", "28", "4"),
    ("костанай", "kostanay", "391010000", "51", "12"),
    ("кызылорда", "kyzylorda", "431010000", "45", "3"),
    ("уральск", "uralsk", "271010000", "19", "7"),
    ("петропавловск", "petropavlovsk", "591010000", "7", "11"),
    ("актау", "aktau", "471010000", "5633", "9"),
    ("туркестан", "turkestan", "512610000", "74", "6"),
    ("кокшетау", "kokshetau", "111010000", "24", "13"),
    ("талдыкорган", "taldykorgan", "191010000", "40", "8"),
    ("экибастуз", "ekibastuz", "552210000", "85", "1"),
    ("темиртау", "temirtau", "352410000", "65", "5"),
    ("балхаш", "balkhash", "351610000", "72", "5"),
)
_ALIASES = {"нур-султан": "астана", "nur-sultan": "астана"}

KASPI_CITY_IDS = {name: kaspi for ru, lat, kaspi, _, _ in _CITIES for name in (ru, lat)}
OLX_CITY_IDS = {name: olx for ru, lat, _, olx, _ in _CITIES for name in (ru, lat)}
# A city's region, by its name or its OLX city_id ("1" -> "8").
OLX_REGION_IDS = {key: region for ru, lat, _, olx, region in _CITIES for key in (ru, lat, olx)}


def _key(name: str | None) -> str:
    key = (name or "").strip().lower()
    return _ALIASES.get(key, key)


def kaspi_city_id(name: str | None) -> str | None:
    """Kaspi ``cityId`` for a city name (Russian or Latin, any case), else None."""
    return KASPI_CITY_IDS.get(_key(name))


def olx_city_id(name: str | None) -> str | None:
    """OLX.kz ``city_id`` for a city name (Russian or Latin, any case), else None."""
    return OLX_CITY_IDS.get(_key(name))


def olx_region_id(city: str | None) -> str | None:
    """OLX.kz ``region_id`` (the city's oblast) for a city name or OLX city_id, else None."""
    return OLX_REGION_IDS.get(_key(city))


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


__all__ = [
    "KASPI_CITY_IDS",
    "OLX_CITY_IDS",
    "OLX_REGION_IDS",
    "kaspi_city_id",
    "olx_city_id",
    "olx_region_id",
    "resolve_city",
]
