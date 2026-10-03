# ODD Feature: ari-concurrency (L1 + L2)

## Objective
Make Ari stable under load and able to handle N tasks at once without hanging,
keeping Ari as an orchestrator that delegates heavy work to bounded background
agents — never doing heavy work on the interactive path and never thrashing the
host.

## Problem
1. **Chat path is serial + in-process.** `main.py` builds the python-telegram-bot
   `Application` with no `concurrent_updates`, so PTB defaults to processing one
   update at a time. `_dispatch` awaits the full Claude turn before the next
   update is pulled, so one slow chat blocks every other chat.
2. **Background delegation is unbounded.** Missions, scheduled tasks, and coding
   already run as background coroutines, but `claim_pending()` claims all pending
   work and spawns it all at once with no cap → a flood spawns many `claude`
   subprocesses simultaneously and can thrash the host.

(Per-turn MCP cold start — the timeout root cause — is lever **L3**, deferred to
a later feature.)

## Scope
- **L1 — Concurrent chat:** enable `concurrent_updates` and serialize per-user so
  concurrency across users never corrupts a single user's working memory.
- **L2 — Bounded background pool:** a shared, configurable semaphore that
  missions, scheduled tasks, and coding acquire before launching their Claude
  work. The interactive chat path does NOT draw from this pool (pools are
  separated, per owner decision).

## Out of scope (noted follow-ups)
- **Cross-path same-user memory race:** a background mission and an interactive
  message for the *same* user can already run concurrently and race that user's
  working memory. This pre-exists and is not caused by this change. A later
  follow-up can unify the per-user lock across the chat and background paths.
- **L3 — warm/persistent MCP** to kill per-turn cold start.

## Constraints
- Strict TDD (RED → GREEN → REFACTOR) with observed evidence per task.
- Follow existing hexagonal layering; keep new units small and single-purpose.
- Two new settings knobs with sane, host-friendly defaults.

## Design decisions
- **Separated pools** (owner-chosen): chat capped via `concurrent_updates`;
  background capped via a dedicated `AgentPool` semaphore. Chat never waits
  behind a background flood.
- **AgentPool gate location:** wrapped around the background execution inside the
  runners (`MissionRunner._execute`, `RunDueItems._run_task`,
  `CodingRequestRunner._plan`), so the whole background turn — including its
  Claude subprocess — is bounded. Chat stays off this pool.
- **Per-user serialization:** a `KeyedLocks` helper (one `asyncio.Lock` per
  `user_id`) acquired in `_dispatch`.
- **Defaults:** `max_concurrent_chats = 8`, `max_background_agents = 3`.

## Tasks
- [x] **T1 — Settings:** add `max_concurrent_chats` (8) and
  `max_background_agents` (3) to `Settings`, with a defaults test.
- [x] **T2 — KeyedLocks:** per-key async lock registry; same key serializes,
  different keys run concurrently.
- [ ] **T3 — AgentPool:** bounded-semaphore async context manager; caps
  concurrency to N, queues the rest; configurable size.
- [ ] **T4 — Wire L1:** set `concurrent_updates(max_concurrent_chats)` on the
  builder and acquire the per-user lock around the dispatch turn.
- [ ] **T5 — Wire L2:** construct one `AgentPool` in `_post_init`, inject it into
  the three runners, and acquire it around each background turn.

## Acceptance criteria
- Two users chatting concurrently are handled in parallel; two messages from one
  user are serialized.
- With `max_background_agents = 1`, two pending background items run
  sequentially; with N, at most N run at once and the rest queue.
- Settings expose both knobs with the documented defaults.
- All applicable checks green: `uv run pytest` and `uv run ruff check`.

## Checks (runner)
- Tests: `uv run pytest`
- Lint: `uv run ruff check src tests`

## Progress / evidence
- **T1 (Settings)** ✅ TDD RED (`AttributeError: 'Settings' object has no attribute
  'max_concurrent_chats'`) → GREEN (`2 passed`). Test:
  `tests/config/test_settings_concurrency.py`.
- **T2 (KeyedLocks)** ✅ TDD RED (`ModuleNotFoundError: ari.application.concurrency`)
  → GREEN (`3 passed`). Module: `src/ari/application/concurrency/keyed_locks.py`;
  test: `tests/application/test_keyed_locks.py`.

## Next step
Implement T3 (AgentPool) with TDD.
