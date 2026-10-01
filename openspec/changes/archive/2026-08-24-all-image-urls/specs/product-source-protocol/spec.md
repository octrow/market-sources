# product-source-protocol

## ADDED Requirements

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
