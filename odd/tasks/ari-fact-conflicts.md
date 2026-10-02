# Feature: Fact conflict resolution (Frente 2 of Ari memory upgrade)

## Objective
Stop Ari's fact memory from silently corrupting data. Today `upsert_fact` blindly
overwrites (`ON CONFLICT DO UPDATE`), and auto-extraction invents synonym keys, so a
transient mention ("visiting Barcelona") can destroy a real value ("ciudad: Madrid").
Add a judgment layer: detect conflicts, resolve them (supersede/keep/merge), keep a
recoverable history, and reduce key drift at the source.

## Problem / Why
Audit "Ari memory gap analysis: plumbing without judgment": `upsert_fact` silent
overwrite + free-form keys. Chosen approach (user): **LLM judgment + history**.

## Scope (authorized)
Behind MemoryPort + MemoryMaintainer + AriTools.recordar_dato.
- `src/ari/infrastructure/persistence/db.py` — NEW `facts_history` table in `_SCHEMA`.
- `src/ari/domain/memory/memory_port.py` + adapter — `get_fact`, `add_fact_history`, `get_fact_history`.
- `src/ari/domain/memory/fact_keys.py` (NEW) — pure key normalization.
- `src/ari/application/memory_maintainer.py` — conflict-aware extraction with LLM judge + history; pass existing keys to the extraction prompt.
- `src/ari/application/ari_tools.py` — `recordar_dato` records history on overwrite + shows old value in receipt.
- `tests/fakes.py` — FakeMemory: get_fact/add_fact_history/get_fact_history + history store.
- Tests across touched layers.

Out of scope: Frente 3 (importance/decay). Recalls conflict handling (facts only here).

## Constraints
- `facts_history` added to `_SCHEMA` so BOTH `connect()` and `open_existing()` (the MCP
  server process that runs recordar_dato) create it. `CREATE TABLE IF NOT EXISTS` only.
- Safe default: on LLM judge failure or unparseable verdict, KEEP the old value and record
  the attempted new value in history — NEVER destroy data on uncertainty.
- Manual `recordar_dato` stays a direct overwrite (explicit user intent) but records history.
- Graceful degradation: a judge/LLM error must never break extract_facts (it already
  swallows exceptions — keep that).
- Artifacts in English; Spanish only in user-facing bot copy (recordar_dato receipt).
- Ruff line-length 100.

## TDD
- Mode: STRICT. Runner: `.venv/bin/python -m pytest` (py3.12; the PATH pytest is py3.9, fails).
- Every task: observed RED → GREEN → REFACTOR. No invented evidence.

## Checks (Verification)
- Targeted: `.venv/bin/python -m pytest tests/domain tests/infrastructure/test_sqlite_memory.py tests/application/test_memory_maintainer.py tests/application/test_ari_tools_agenda.py -q`
- Full: `.venv/bin/python -m pytest -q -m "not slow"`
- Lint: `ruff check src tests` (new files must be clean).

## Tasks
- [x] T1 — `facts_history` table in `_SCHEMA` (id, user_id, key, old_value, new_value, resolution, created_at) + index.
- [x] T2 — Port + adapter: `get_fact`, `add_fact_history`, `get_fact_history`. Tests in test_sqlite_memory.py.
- [x] T3 — NEW `fact_keys.normalize_key` (lowercase, strip, whitespace→_). Pure, unit-tested.
- [x] T4 — MemoryMaintainer conflict judge: normalize key; read existing; if value differs, LLM judge → supersede/keep/merge; apply + add_fact_history; pass existing keys to extraction prompt; safe default KEEP+log on failure/unknown verdict. Tests with MultiReplyFakeLLM.
- [x] T5 — `recordar_dato` normalizes key, logs history, shows prior value in receipt (`olvidar_dato` normalizes too). Tests.
- [x] T6 — FakeMemory: get_fact/add_fact_history/get_fact_history + in-memory history.

## Work-unit commits (plan)
- C1 (foundation): T1 + T2 + T6 + tests.
- C2 (judgment): T3 + T4 + tests.
- C3 (manual receipt): T5 + tests.

## Delivery
Strategy: ask-on-risk. Forecast ≈ 400–550 authored lines (judge + migration + tests) — may
exceed the ~400 heuristic; if so, deliver as the 3 work-unit commits / chained PRs.
Branch: `feat/fact-conflicts`. DO NOT push to main manually — the live bot auto-merges+pushes
feature branches (see Engram: "Ari live bot auto-merges AND auto-pushes to origin/main").

## Route
Delegated writer (sonnet) with airtight TDD spec; orchestrator verifies EVERY claim with
`git diff --stat` on named files + own test run (prior writer fabricated TDD evidence — see
Engram learning). Fix inline if fabrication recurs.

## Progress / Evidence
- DONE — commit `3a1c38d` "feat(memory): resolve fact conflicts with LLM judgment and history" on branch feat/fact-conflicts (from main 0ad2c1c). Delivered as ONE atomic feature commit (main clean; splitting shared fakes.py/test files across commits added risk without value).
- Verification: `.venv/bin/python -m pytest -q -m "not slow"` → 718 passed, 2 skipped (+25 tests). `ruff check` on all changed/new files → all passed.
- Writer was HONEST this time (git diff confirmed the named test files actually changed). Orchestrator still reviewed the judge logic by hand: safe KEEP default on empty/unknown/exception verified; history logged even on keep (recoverable); existing keys passed to extraction prompt.
- NOT pushed to main manually — left for the live bot / user (bot auto-merges+pushes feature branches).

## Next step
User merges/pushes (or the bot auto-merges feat/fact-conflicts). Then Frente 3: importance
calculation + a decay/consolidation job on the existing Scheduler (main.py).
