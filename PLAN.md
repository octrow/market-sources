# План: market-sources

Открытые баги, планы и идеи. Обновлено 2026-11-05. Закрытый пункт удаляется или получает ✅ со ссылкой на коммит.

## Баги

- [ ] MEDIUM (не подтверждён): Kaspi режет id карточки до ведущих цифр, варианты "16535441a" и "16535441b" делят один `product_id`; нужен живой запуск на fashion-запросе, потом ключ по полному id или `configSku` — [logic-map-fixes #10](docs/plans/2026-11-05-logic-map-fixes.md)
- ✅ 2026-11-05 MEDIUM: `OlxSource._offer_cache` теперь LRU на 5000 записей (`src/market_sources/lru.py`), b677a7c
- ✅ 2026-11-05 MEDIUM: `_facets` и карты sku/city в Kaspi ограничены LRU, b677a7c
- [ ] MEDIUM: семафор `PoliteClient` держится через паузы retry и таймауты, два зависших запроса стопорят клиента; нет cooldown после N подряд 403/429 — `src/market_sources/polite_http.py:229`
- ✅ 2026-11-05 MEDIUM: `search_all` по-прежнему пропускает упавшие страницы, но теперь пишет по каждой warning (offset, запрос, тип и текст ошибки) и итоговый "N of M pages failed, result is partial"; флага `partial` нет по решению владельца — ef8891d
- [ ] MEDIUM: у каждого города и региона свой `PoliteClient`, поэтому N городов умножают конкурентность и риск бана на N — `src/market_sources/olx/source.py:277`, [system-design §2](docs/system-design.md)
- [ ] MEDIUM: Ozon держится на ручном bootstrap cookies, он уже протухал (`ozon_state.json.expired`), алерта и автоматического fallback нет — `src/market_sources/playwright_session.py`

## Планы и фичи

- [ ] Закрыть "Done when" плана логики: у каждой находки ✅ или решение "оставить как есть"; Known gaps в карте перечисляют все открытые HIGH и MEDIUM — [logic-map-fixes](docs/plans/2026-11-05-logic-map-fixes.md)
- [ ] Следующая ступень при 403/429 > ~5% вызовов за сутки: пул прокси и кэш результатов у потребителя — [system-design §6, §8](docs/system-design.md)

## Идеи

- [ ] Один debug-лог на запрос со статусом и временем, чтобы мерить p95 и долю блокировок — [system-design Risks](docs/system-design.md#risks)
- [ ] Бэкап сессии Ozon (`ozon_state.json`) или более простое восстановление без человека с экраном — [system-design Risks](docs/system-design.md#risks)
- [ ] Кэш страницы Ozon дольше 60 с, если `offers` и `specs` вызывают с разрывом (сейчас две навигации) — `src/market_sources/ozon/source.py:33`

## Подробные планы

- [docs/plans/2026-11-05-logic-map-fixes.md](docs/plans/2026-11-05-logic-map-fixes.md): находки по карте логики, открыт #10
- [docs/system-design.md](docs/system-design.md): разбор архитектуры, нагрузки, отказов и рисков (2026-11-05); сводка по всем репо: `~/dev/docs/system-design-report-2026-11-05.md`
- [docs/logic-map.md](docs/logic-map.md): как запрос превращается в `Raw*` модели
- [openspec/specs/](openspec/specs/): внутренние спеки; активных openspec changes нет
- Открытых GitHub issues нет
