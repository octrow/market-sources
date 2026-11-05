"""Bounded caches: LRUDict itself and its use in the OLX and Kaspi sources."""

from __future__ import annotations

import pytest

from market_sources.kaspi.source import KaspiSource
from market_sources.lru import LRUDict
from market_sources.olx.source import OLX_OFFER_CACHE_SIZE, OlxSource


def test_lru_evicts_the_least_recently_used_entry():
    cache: LRUDict[str, int] = LRUDict(2)
    cache["a"] = 1
    cache["b"] = 2
    assert cache.get("a") == 1  # refresh "a"
    cache["c"] = 3
    assert "b" not in cache
    assert set(cache) == {"a", "c"}


def test_lru_rejects_a_non_positive_size():
    with pytest.raises(ValueError):
        LRUDict(0)


def test_olx_offer_cache_is_bounded():
    source = OlxSource(client=object())  # type: ignore[arg-type]
    source._cache_offers({"data": [{"id": i} for i in range(OLX_OFFER_CACHE_SIZE + 10)]})
    assert len(source._offer_cache) == OLX_OFFER_CACHE_SIZE
    assert "0" not in source._offer_cache


def test_kaspi_maps_are_bounded():
    source = KaspiSource(client=object())  # type: ignore[arg-type]
    for cache in (source._api_ids, source._product_cities, source._facets):
        assert isinstance(cache, LRUDict)
