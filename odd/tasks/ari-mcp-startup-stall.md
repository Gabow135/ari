# ODD feature: ari-mcp-startup-stall

## Objective
Stop Ari from timing out on (nearly) every turn because a dead MCP server hangs
during per-turn cold start. Make a stalled server fail fast and be skipped so Ari
still answers, instead of burning the whole chat budget.

## Problem / Why
Verified root cause (engram obs #114, still live today): Ari spawns a fresh
`claude` CLI per message and `ToolPolicy.turn` loads ALL owner MCP servers each
turn. Today that is 6 cold starts via `uvx`/`npx -y`: google, mysql, filesystem,
and THREE email/IMAP servers (email_corp, email_gmail, email_hotmail). Several
open network connections at startup; `email_corp` points at `${ARI_IMAP_HOST}:993`,
flagged as a host with no :993 listener → hangs. There is NO `MCP_TIMEOUT` set
(confirmed: none in src), so a stalled server burns the CLI default and stacks
past the chat timeout. Symptom: Ari replies only the canned timeout message, even
to trivial messages.

## Scope
- IN: inject an MCP startup timeout env var for every `claude` spawn (chat + coder),
  made configurable via settings.
- OUT (later slices / need live/.env): removing or repairing the broken email
  server; not cold-starting every MCP server on each turn.

## Constraints
- Keep `claude_cli_env` login semantics intact (returns None with no token →
  inherits host login; the warning in main.cli_env must still fire).
- TDD: enabled. Runner: `uv run pytest`.

## Tasks
- [x] T2 — Added `mcp_startup_timeout_seconds` setting (default 20) and
  `with_mcp_startup_timeout(env, seconds)` in claude_env.py setting `MCP_TIMEOUT`
  (ms) on the final subprocess env; wired via main.cli_env so chat AND coder get
  it. TDD RED→GREEN. Evidence: `cli_env(Settings)` smoke → `MCP_TIMEOUT=20000`;
  689 passed, 2 skipped (non-live suite). Files: settings.py, claude_env.py,
  main.py, tests/infrastructure/test_claude_env.py, tests/config/test_settings_tools.py.
  Route: direct inline. NOTE: claude CLI abort-vs-skip behavior on a timed-out
  server is not officially documented (claude-code-guide could not confirm);
  fast-fail is strictly better than the prior unbounded hang either way.
- [x] T3 — CLOSED BY DECISION (2026-10-03): leave email_corp as is. MCP_TIMEOUT
  already stops it from blocking the turn; the only cost is ~20s of wasted startup
  budget per turn on a dead server. Not disabled (keeps the corp-email capability);
  repairing the host (open :993 / DNS) is server-side, out of this repo's scope.
  .env stayed permission-denied so the host was never read or probed.
- [ ] T4 — (optional, bigger) Avoid cold-starting all MCP servers each turn.

## Related (already shipped this session, separate change — uncommitted)
- chat_timeout_seconds 180 → 900 and reworded TIMEOUT_REPLY backstop copy. That is
  necessary-but-NOT-sufficient: without T2 it just makes a stalled turn wait 15 min
  before failing. T2 is the real symptom fix.

## Acceptance
- `MCP_TIMEOUT` is present in the env passed to every `claude` subprocess.
- A server that fails to start within the timeout no longer consumes the full
  chat budget (bounded fast-fail).
- Checks: `uv run pytest tests/infrastructure/test_claude_env.py tests/config`.
