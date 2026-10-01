# Tasks: all-image-urls

- [ ] 1.1 Write failing tests for the three scenarios against saved payload fixtures (kaspi, olx; ozon analogous to kaspi if its fixture carries several images)
- [ ] 1.2 Add `image_urls: list[str] = []` to `RawProduct` in `base.py`
- [ ] 1.3 Fill `image_urls` in `kaspi/parse.py`, `olx/parse.py`, `ozon/parse.py`; keep `image_url` = first entry
- [ ] 1.4 Full suite green; `openspec validate --strict` clean
