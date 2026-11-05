# Agents: start here

1. `docs/logic-map.md`: how a query or product id becomes `Raw*` models: every step, every branch, the module behind it.
2. `docs/plans/`: work in progress; `2026-11-05-logic-map-fixes.md` lists the known logic gaps.
   Open bugs, plans and ideas: [PLAN.md](PLAN.md).
3. `openspec/specs/`: internal specs per capability.

Rules:
- Change a step or a branch → update `docs/logic-map.md` in the same commit.
- `tests/test_logic_map.py` fails when a `Module:` line in the map points to a module that is gone.
