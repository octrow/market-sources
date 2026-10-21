# product-source-protocol Specification

## Purpose
TBD - created by archiving change all-image-urls. Update Purpose after archive.

## Requirements

### Requirement: A product exposes every listing photo URL, in order

`RawProduct` MUST carry `image_urls: list[str]` holding every photo URL the
site returned for the listing, in the site's order (index 0 = lead photo).
`image_url` MUST remain and equal the first entry when any photos exist —
downstream consumers (buy-smarter ranking) already read it. Raw in, raw out:
the list is whatever the site said, no dedup or validation beyond being URLs.

#### Scenario: parser preserves the full photo array

- **WHEN** a saved Kaspi payload whose card has three `previewImages` entries is parsed
- **THEN** the product's `image_urls` has three URLs in payload order and `image_url` equals the first
- **Anchor**: `tests/test_kaspi_parse.py::test_all_image_urls_preserved`

#### Scenario: OLX offer photos are all kept

- **WHEN** a saved OLX payload whose offer has multiple `photos` entries is parsed
- **THEN** every photo URL appears in `image_urls` in payload order
- **Anchor**: `tests/test_olx_parse.py::test_all_image_urls_preserved`

#### Scenario: a listing with no photos

- **WHEN** a payload whose card has an empty photo array is parsed
- **THEN** `image_urls` is empty and `image_url` is None
- **Anchor**: `tests/test_kaspi_parse.py::test_no_photos_empty_list`

### Requirement: A product carries the source's brand, category and endpoint id

`RawProduct` MUST carry `brand: str | None`, `review_id: str | None`,
`official_partner: bool | None` and `category_path: list[str]`, all defaulting
to "unknown" (`None` / empty). `brand` is the source's string verbatim, "Без
бренда" included. `review_id` is set only when the source's per-product
endpoints want an id other than `product_id` (Kaspi: `configSku`).
`ProductSource.reviews` MAY take extra keyword-only options; a call with
`product_id` alone MUST keep working.

#### Scenario: a Kaspi fashion variant keeps its configSku

- **WHEN** a Kaspi card with id "16535441b" and configSku "121564477" is parsed
- **THEN** `product_id` is "16535441", `review_id` is "121564477", and brand and category path are kept
- **Anchor**: `tests/test_kaspi.py::test_a_fashion_card_keeps_brand_category_and_config_sku`

#### Scenario: KaspiSource calls a variant's endpoints by configSku

- **WHEN** `search` returned the variant and `offers`/`reviews`/`specs` are called with its `product_id`
- **THEN** each request goes to the configSku, and results stay keyed by `product_id`
- **Anchor**: `tests/test_kaspi.py::test_variant_calls_use_the_config_sku_remembered_from_search`
