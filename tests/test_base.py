"""The contract: model defaults and ``ProductSource`` conformance."""

from __future__ import annotations

import pytest

from market_sources import KaspiSource, OlxSource, OzonSource
from market_sources.base import ProductSource, RawOffer, RawProduct, RawReview


def test_raw_product_defaults():
    product = RawProduct(source="kaspi", product_id="1", title="X")
    assert product.currency == "KZT"
    assert product.specs_available is True
    assert (product.price, product.url, product.rating, product.review_count) == (
        None,
        None,
        None,
        None,
    )


def test_raw_offer_requires_a_price_and_defaults_to_in_stock():
    offer = RawOffer(source="olx", product_id="1", price=100)
    assert offer.in_stock is True
    assert offer.currency == "KZT"
    with pytest.raises(Exception):  # noqa: B017 — pydantic ValidationError
        RawOffer(source="olx", product_id="1")


def test_raw_review_text_defaults_to_empty():
    assert RawReview(source="ozon", product_id="1").text == ""


@pytest.mark.parametrize("source_cls", [KaspiSource, OlxSource, OzonSource])
def test_every_adapter_satisfies_the_protocol(source_cls):
    """Structural check — no adapter is instantiated, so nothing touches the network."""
    for method in ("search", "offers", "reviews", "specs"):
        assert callable(getattr(source_cls, method))
    assert isinstance(source_cls.name, str)


@pytest.mark.parametrize(
    ("source_cls", "double"),
    [(KaspiSource, "kaspi"), (OlxSource, "olx"), (OzonSource, "ozon")],
)
def test_adapters_are_runtime_checkable_product_sources(source_cls, double):
    class Stub:
        pass

    instance = source_cls.__new__(source_cls)  # no client/browser construction
    assert isinstance(instance, ProductSource)
    assert not isinstance(Stub(), ProductSource)
    assert instance.name == double
