"""City name -> Kaspi cityId / OLX city_id."""

import pytest

from market_sources.cities import kaspi_city_id, olx_city_id, resolve_city


def test_a_city_name_resolves_in_russian_or_latin_any_case():
    assert kaspi_city_id(" Шымкент ") == "511010000"
    assert kaspi_city_id("SHYMKENT") == "511010000"
    assert olx_city_id("Шымкент") == "47"
    assert olx_city_id("almaty") == "1"


def test_astana_answers_to_its_old_name():
    assert kaspi_city_id("Нур-Султан") == "710000000"
    assert olx_city_id("nur-sultan") == "87"


def test_an_unknown_or_missing_name_is_none():
    assert kaspi_city_id("Конаев") is None
    assert olx_city_id("") is None
    assert kaspi_city_id(None) is None


def test_resolve_city_passes_ids_through_and_rejects_unknown_names():
    assert resolve_city("750000000", kaspi_city_id) == "750000000"
    assert resolve_city("Астана", olx_city_id) == "87"
    assert resolve_city(None, kaspi_city_id) is None
    with pytest.raises(ValueError):
        resolve_city("Atlantis", olx_city_id)
