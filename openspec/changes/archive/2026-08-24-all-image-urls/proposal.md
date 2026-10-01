# Change: Expose every listing photo URL

## Why

taste-vision needs 1-6 photos per product (different views of one item), but
all three parsers deliberately take only the first entry of the photo arrays
the sites already return (`previewImages`, `photos`, `tileImage`). The data is
on the wire and thrown away at parse time.

## What Changes

- `RawProduct` gains `image_urls: list[str]` (listing order). `image_url`
  stays and remains the first entry — existing consumers keep working.
- Kaspi, OLX and Ozon parsers fill `image_urls` from the arrays they already
  read.

## Impact

- Affected specs: `product-source-protocol` (new spec file).
- Affected code: `base.py`, `kaspi/parse.py`, `olx/parse.py`, `ozon/parse.py`,
  their payload-fixture tests.
