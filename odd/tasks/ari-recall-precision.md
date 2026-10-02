# Feature: Recall precision (Frente 1 of Ari memory upgrade)

## Objective
Turn Ari's semantic recall from "pure cosine top-k" into a ranked recall weighted
by similarity + recency + importance, with a relevance threshold and store-time
dedup, so Ari injects the *right* memories at the right time and the recall store
stops rotting over time.

## Problem / Why
Audit (see Engram: "Ari memory gap analysis: plumbing without judgment") found:
- `store_recall` always receives `metadata={}` — the field is dead.
- `retrieve_recalls` exposes neither similarity score nor `created_at`, so there is
  no way to weight by relevance or recency. It is cosine-only, fixed top-5.
- No distance threshold: even when nothing is relevant, the 5 "least irrelevant"
  recalls get injected as context noise.
- Every turn stores a "User:…/Ari:…" recall with no dedup → unbounded growth and
  degrading retrieval quality.

## Scope (authorized)
Behind `MemoryPort` + `HandleMessage` only. The port *signature* does NOT change;
only the richness of the returned `Recall` and the retrieval/store logic.

In scope:
- `src/ari/domain/memory/entities.py` — add `score` to `Recall` (`created_at` already exists).
- `src/ari/infrastructure/memory/sqlite_memory_adapter.py` — `retrieve_recalls` SELECTs
  `r.created_at` + `v.distance` and populates them on `Recall`.
- `src/ari/domain/memory/recall_ranker.py` (NEW) — pure ranking service.
- `src/ari/application/handle_message.py` — `_retrieve` (fetch k×mult, rank, threshold,
  top-k) and `_store_recall` (dedup + populate metadata).
- `src/ari/config` Settings — new knobs (weights, threshold, candidate multiplier, dedup threshold).
- `src/ari/main.py` — wire ranker + settings into `HandleMessage`.
- `tests/fakes.py` — `FakeMemory` carries score/created_at so tests reflect real behavior.
- Tests across the touched layers.

Out of scope: importance CALCULATION (Frente 3 — here importance is stored with a
default and only consumed by the ranker), conflict resolution (Frente 2), schema migration.

## Constraints
- No schema migration: `recalls.created_at` exists, the vec index exposes `distance`,
  importance lives in the existing `metadata_json`.
- Graceful degradation in `_retrieve`/`_store_recall` must be preserved.
- Artifacts in English (code, comments, identifiers). Spanish only in user-facing bot copy.
- Ruff line-length 100.

## TDD
- Mode: STRICT (session config). Runner: `pytest` (asyncio_mode=auto, pythonpath=src).
- Every task: observed RED → GREEN → REFACTOR. No invented evidence.

## Checks (Verification)
- Targeted: `pytest tests/domain tests/infrastructure/test_sqlite_memory.py tests/application/test_handle_message.py -q`
- Full (no model downloads): `pytest -q -m "not slow"`
- Lint: `ruff check src tests`

## Tasks
- [ ] T1 — `Recall.score: float | None = None` on the entity. (route: inline/writer)
- [ ] T2 — Adapter `retrieve_recalls` returns `created_at` + `score` (= distance). Extend `test_sqlite_memory.py`.
- [ ] T3 — NEW `RecallRanker`: combined score (w_sim·sim + w_rec·recency_decay + w_imp·importance) + relevance threshold. Pure, unit-tested.
- [ ] T4 — `_retrieve` fetches `k × candidate_multiplier`, ranks, applies threshold, returns top-k. Integration test.
- [ ] T5 — `_store_recall` dedup (skip near-identical via retrieve k=1 distance check) + populate `metadata` (`type`, `importance` default). Dedup + metadata tests. Update `FakeMemory`.
- [ ] T6 — Settings knobs + `main.py` wiring.

## Work-unit commits (plan)
- C1 (read path): T1 + T2 + T3 + tests.
- C2 (integration): T4 + T5 + fakes + tests.
- C3 (wiring): T6.

## Delivery
Strategy: ask-on-risk (default). Forecast ≈ 250–350 authored lines → likely single PR.
Branch: `feat/recall-precision`.

## Progress / Evidence
- (pending) RED/GREEN evidence and commit SHAs recorded per task as implemented.

## Next step
Delegate writer (sonnet, strict TDD) to implement C1→C3; orchestrator spot-checks and commits per work unit.
