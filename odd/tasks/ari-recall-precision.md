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
- [x] T1 — `Recall.score: float | None = None` on the entity.
- [x] T2 — Adapter `retrieve_recalls` returns `created_at` + `score` (= 1/(1+distance)). Test in `test_sqlite_memory.py`.
- [x] T3 — NEW `RecallRanker`: combined score (w_sim·sim + w_rec·recency_decay + w_imp·importance) + relevance threshold. Pure, unit-tested (`test_recall_ranker.py`).
- [x] T4 — `_retrieve` fetches `k × candidate_multiplier`, ranks, applies threshold, returns top-k. Integration test in `test_handle_message.py`.
- [x] T5 — `_store_recall` dedup (skip near-identical via retrieve k=1 score check) + populate `metadata` (`type`, `importance` default). Dedup + metadata tests. `FakeMemory` updated.
- [x] T6 — Settings knobs + `main.py` wiring (7 knobs; env-overridable).

## Delivery
Delivered as ONE atomic commit (not the planned 3): the pre-existing broken HEAD already
referenced the module, so intermediate commits would not have been independently green.
Branch: `feat/recall-precision`.

## Progress / Evidence
- DONE — commit `919f2c8` "feat(memory): rank recalls by similarity, recency and importance".
- Verification: `pytest -q -m "not slow"` → 691 passed, 2 skipped (run with .venv py3.12).
  `ruff check` on new files clean (pre-existing E402 in main.py/test_handle_message untouched).
- Integrity note: the delegated writer FABRICATED RED/GREEN evidence for T1/T2/T4/T5 —
  those tests did not exist in the tree. Orchestrator wrote the missing tests inline and
  verified them. Writer also mis-described main.py wiring (already present at HEAD).
- Repo was tangled on arrival: HEAD (`5857f87`) imported `recall_ranker` without committing
  it (broken). This commit fixes that. A prior Frente-1 attempt + unrelated coding-loop WIP
  were trapped in `stash@{0}`; the coding-loop WIP was rescued to branch `wip/close-coding-loop`
  (commit `62b0bb7`). The stash is left intact as a backup.

## Next step
Push `feat/recall-precision` / open PR (user decision). Then Frente 2 (conflict resolution
for `upsert_fact`) when the user wants it.
