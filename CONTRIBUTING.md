# Contributing

**English** | [Русский](CONTRIBUTING.ru.md)

Bug reports and pull requests are welcome. Sites change their APIs without notice, so a report that says
"Kaspi search returns nothing since yesterday" with the query and the error is already useful.

## Set up

```bash
git clone https://github.com/octrow/market-sources.git
cd market-sources
uv sync --all-extras
uv run pytest
uv run ruff check .
```

The tests use no network, no browser and no real clock, so they run in a few seconds.

## Rules for a change

- Keep the adapter split: `client.py` (or `browser.py`) does I/O, `parse.py` is pure functions over decoded
  `dict`s, `source.py` wires them into a `ProductSource`. A parser fix comes with a test built from a
  minimal payload, not a saved full response.
- Models stay source-shaped (`RawProduct`, `RawOffer`, `RawReview`). Normalization belongs to the consumer.
- Don't add a dependency for something a few lines can do. New optional features go behind an extra and a
  lazy import, so `import market_sources` keeps working with only `httpx` and `pydantic`.
- Behavior changes to `polite_http`, `playwright_session` or the `ProductSource` protocol should update the
  matching spec in `openspec/specs/`.
- Commit messages: `<type>: <description>` (`feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `ci`).

## Be a good citizen

The library is paced and capped on purpose (small semaphore, jittered delays, `Retry-After` honored).
Pull requests that remove those limits or add ways around a site's access controls won't be merged.
