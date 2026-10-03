# ari-file-logging

## Objective
Give Ari a persistent, rotating log file so its runtime behavior (especially chat
turn timeouts) can be followed after the fact, and make the timeout path record
*why* a turn stalled.

## Problem / Why
Live Ari only answers with the canned `TIMEOUT_REPLY` on every message. Root cause
(verified this session): each chat turn spawns a fresh `claude` CLI that cold-starts
up to 5 owner MCP servers; stalled servers push the turn past the 180s budget →
`LLMTimeoutError` → canned reply. There is **no log file** to confirm this live:
`logging.basicConfig` sends everything to the controlling tty (ttys014) and it is
lost. On timeout the CLI's stderr — where a hung MCP server prints its error — is
discarded, so even the console log never shows the cause.

## Scope (authorized)
- A. Rotating file logging, configurable via settings/env, wired at `main()` start.
- B. Capture and log the `claude` CLI stderr tail when a turn times out.
Out of scope: fixing the MCP/IMAP stall itself (separate follow-up), changing the
180s budget.

## Route
Inline (direct). Touches ~4 files + 2 test files. Chosen inline over a delegated
writer deliberately: Strict TDD is on and a prior session recorded a delegated
writer fabricating RED/GREEN evidence — running the tests first-hand here
guarantees the RED→GREEN evidence is real and observed by the orchestrator.

## TDD mode
Strict TDD: enabled (session config). Runner: `uv run pytest` (pyproject,
asyncio_mode=auto, testpaths=tests).

## Tasks
- [x] T1 — logging_setup module: console + RotatingFileHandler, idempotent,
      configurable path/level. RED observed (ModuleNotFoundError) → GREEN (4/4).
      src/ari/infrastructure/logging_setup.py + tests/infrastructure/test_logging_setup.py.
- [x] T2 — settings: added `log_file="./logs/ari.log"` and `log_level="INFO"`;
      wired `setup_logging(...)` as first line of `main()`; import-time basicConfig
      kept as fallback. .gitignore now ignores `logs/` and `*.log`.
- [x] T3 — stream_json: on timeout, drain + log the CLI stderr tail; kept the
      concurrent stderr read (outer-scope task) to avoid pipe deadlock. RED
      observed (caplog empty) → GREEN (18/18, existing timeout tests still pass).
- [x] T4 (emergent) — test isolation: `main()` now calls setup_logging, so the 4
      tests that invoke `main()` (test_main_shutdown, test_main_access x2,
      test_bot_errors) were polluting the repo's real ./logs. Fixed by setting
      `ARI_LOG_FILE=""` (console-only) in each. Verified: full suite no longer
      creates ./logs.

## Acceptance — MET
- `uv run pytest` green: 751 passed, 4 skipped (pre-existing skips).
- Full suite no longer creates ./logs (isolation verified).
- Real smoke: setup_logging('./logs/ari.log') creates the file and writes INFO +
  the WARNING line shape a real timeout will emit.
- New source + test files pass `ruff` clean (repo baseline has ~207 pre-existing
  ruff findings; not a gate).

## Checks
- `uv run pytest -q` → 751 passed, 4 skipped ✓
- `ruff` clean on new/changed source files ✓

## Progress
- All tasks done and verified first-hand (inline TDD, real RED→GREEN).
- Gotcha hit: a `git stash`/`pop` used to compare ruff baseline conflicted on
  uv.lock (because `uv run` re-touches it) and left changes stashed; recovered by
  cleaning uv.lock then popping. Don't `git stash` around `uv run`.
- SHIPPED: committed as `0265d21` and pushed to `origin/main`
  (af4b650..0265d21), fast-forward, alongside the pre-existing `/enroll` commit
  60d8054. User restarts Ari automatically from main.
- Follow-up (separate feature): the actual MCP/IMAP stall that causes the
  timeouts; this feature only makes it observable.
