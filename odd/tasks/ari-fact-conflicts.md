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
- [ ] T1 — `facts_history` table in `_SCHEMA` (id, user_id, key, old_value, new_value, resolution, created_at) + index.
- [ ] T2 — Port + adapter: `get_fact(user_id,key)->Fact|None`, `add_fact_history(user_id,key,old,new,resolution)`, `get_fact_history(user_id,key)->list[dict]`. Tests in test_sqlite_memory.py.
- [ ] T3 — NEW `fact_keys.normalize_key` (lowercase, strip, spaces→_, collapse). Pure, unit-tested.
- [ ] T4 — MemoryMaintainer conflict judge: normalize key; read existing; if value differs, LLM judge → supersede/keep/merge; apply + add_fact_history; pass existing keys to extraction prompt; safe default = keep+log on failure. Tests with FakeLLM returning verdicts.
- [ ] T5 — `recordar_dato`: read old, upsert, add_fact_history; receipt shows old value when it changed. Tests.
- [ ] T6 — FakeMemory: get_fact/add_fact_history/get_fact_history + in-memory history.

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
- (pending)

## Next step
Delegate writer for C1→C3; verify + commit per work unit; leave merge/push to the user/bot.
