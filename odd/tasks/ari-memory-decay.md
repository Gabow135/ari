# Feature: Importance + decay (Frente 3 of Ari memory upgrade)

## Objective
Make recall importance reflect real use, and forget what stays irrelevant. Today every
recall sits at a static importance 0.5 and nothing is ever pruned (unbounded growth). Add
use-based reinforcement + a scheduled decay/prune job, with pinned recalls protected.

## Problem / Why
Audit "Ari memory gap analysis": no importance signal, no decay, no pinning. User chose
**reinforcement by use**: a recall that gets retrieved and used gains importance; one never
used decays and is eventually pruned.

## Model
- Base importance at store: 0.5 (unchanged, lives in `metadata_json`, read by RecallRanker).
- Reinforce on use: when `_retrieve` returns the ranked top-k (the recalls actually injected),
  bump each one's importance: `importance = min(1.0, importance + reinforce_delta)`.
- Decay job (scheduled, throttled): periodically multiply every unpinned recall's importance by
  `decay_factor` (<1), then prune recalls that are `importance < prune_floor` AND older than
  `prune_min_age_days` AND NOT pinned. Reinforcement keeps active recalls above the floor.
- Pinned: a `"pinned": true` flag in `metadata_json` protects a recall from decay and prune.
  No user-facing pin tool in this slice (YAGNI) — the decay job just respects the flag.

## Scope (authorized)
- `src/ari/domain/memory/memory_port.py` + adapter — `bump_recall_importance`, `decay_recalls`, `prune_recalls`.
- `src/ari/application/handle_message.py` — reinforce returned recalls in `_retrieve` (graceful).
- `src/ari/application/memory/consolidator.py` (NEW) — `MemoryConsolidator` scheduled job (throttled via kv).
- `src/ari/config/settings.py` — knobs (reinforce_delta, decay_factor, prune_floor, prune_min_age_days, consolidate_interval_hours).
- `src/ari/main.py` — construct MemoryConsolidator and add it to the Scheduler job list.
- `tests/fakes.py` — FakeMemory: the new methods + a fake kv if needed.
- Tests across touched layers.

Out of scope: user-facing pin tool; fact decay (facts are Frente 2's domain).

## Constraints
- NO schema migration: importance + pinned live in the existing `metadata_json`.
- `prune_recalls` MUST delete from BOTH `recalls` AND `recalls_vec` (vector index keyed by id) to keep them in sync.
- Use SQLite JSON1 (`json_extract`, `json_set`) for importance arithmetic; pinned = `json_extract(metadata_json,'$.pinned') = 1`. (`true` in JSON extracts to 1.)
- Reinforcement and the decay job must degrade gracefully (never break a reply or the scheduler loop; the Scheduler already isolates job failures).
- Artifacts in English. Ruff line-length 100.

## TDD
- Mode: STRICT. Runner: `.venv/bin/python -m pytest` (py3.12).
- Every task: observed RED → GREEN → REFACTOR. No invented evidence.

## Checks (Verification)
- Targeted: `.venv/bin/python -m pytest tests/infrastructure/test_sqlite_memory.py tests/application/test_handle_message.py tests/application/test_consolidator.py -q`
- Full: `.venv/bin/python -m pytest -q -m "not slow"`
- Lint: `ruff check src tests` (new files clean).

## Tasks
- [x] T1 — Adapter + port: `bump_recall_importance(recall_id, delta, cap=1.0)` (read-modify-write metadata importance); `decay_recalls(factor)` (importance*=factor for unpinned, via json_set); `prune_recalls(floor, older_than_iso)` (delete from recalls + recalls_vec where importance<floor AND created_at<older_than AND not pinned; return count). Tests in test_sqlite_memory.py (real sqlite-vec).
- [x] T2 — `_retrieve` reinforces the returned top-k (bump importance), graceful. Tests in test_handle_message.py.
- [x] T3 — NEW `MemoryConsolidator` job: throttle via kv last-run timestamp (like Heartbeat); on due, call decay_recalls + prune_recalls for the configured params; never raise. Tests in test_consolidator.py.
- [x] T4 — Settings knobs + construct MemoryConsolidator in main.py and add its `__call__` to the Scheduler job list.

## Work-unit commits (plan)
- C1: T1 (adapter ops) + tests.
- C2: T2 (reinforcement) + tests.
- C3: T3 (consolidator) + T4 (wiring) + tests.

## Delivery
Strategy: ask-on-risk. Forecast ≈ 400–550 lines. Branch: `feat/memory-decay`.
DO NOT push to main manually — the live bot auto-merges+pushes feature branches.

## Route
Delegated writer (sonnet), airtight spec; orchestrator verifies EVERY claim with `git diff --stat`
on named files + own test run (prior writer fabricated evidence once). Review the vec-table
sync and JSON arithmetic by hand.

## Progress / Evidence
- T1 RED: `AttributeError: 'SqliteMemoryAdapter' object has no attribute 'bump_recall_importance'` → GREEN: 22 passed (test_sqlite_memory.py)
- T2 RED: `TypeError: HandleMessage.__init__() got an unexpected keyword argument 'reinforce_delta'` → GREEN: 21 passed (test_handle_message.py)
- T3 RED: `ModuleNotFoundError: No module named 'ari.application.memory'` → GREEN: 5 passed (test_consolidator.py)
- T4 RED: `'Settings' object has no attribute 'consolidate_interval_hours'` (5 failed) → GREEN: 5 passed (test_settings_decay.py)
- Full suite: 738 passed, 2 skipped, 0 failures (`.venv/bin/python -m pytest -q -m "not slow"`)
- Ruff: new files clean; pre-existing E402/E741/F401 violations in main.py, skill_manager.py, missions files are unchanged

## Next step
DONE — committed on branch feat/memory-decay (one atomic feature commit). 738 tests pass,
ruff clean. Left for the live bot/user to merge+push. Completes the 3-frente memory upgrade
(recall precision → fact conflicts → importance+decay). Verification by orchestrator: hand-
reviewed the recalls_vec sync in prune_recalls (test asserts the id is gone from both tables),
the JSON1 importance arithmetic, graceful reinforcement, and the real Scheduler wiring in main.py.
