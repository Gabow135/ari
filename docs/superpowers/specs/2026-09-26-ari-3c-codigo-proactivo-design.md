# Ari — 3C: Proactive `/code` and `/code` isolation (Design)

- **Date:** 2026-09-26
- **Status:** Draft — pending user approval
- **Part of:** Autonomy (3A ✅ → 3B ✅ → **3C**)
- **Builds on:** 3B internal MCP server (`ari_permissions`, `AriTools`, receipts, per-turn
  config, `after_turn` hook), Phase 2 coding flow (`RequestCoding`, `PendingStore`,
  `ConfirmCoding`, `Workspace`, `ClaudeCodeCoder`).

## 1. Purpose

- In the owner's chat, Ari can **propose and prepare** a code change: a new tool
  `proponer_codigo` starts the existing `/code` planning in the background and sends the
  plan; execution still requires the owner's «dale».
- `/code` planning and execution run isolated from the host's MCP connectors and user
  hooks/plugins, like the chat already is.

Success: "agrega un comando /ping que responda pong" in the owner chat yields a receipt,
then the plan; «dale» executes on a new branch; nobody else (non-owners, scheduled tasks,
heartbeat) can generate plans.

## 2. Locked decisions

| Decision | Choice | Rationale |
|---|---|---|
| Trigger | **A:** tool `proponer_codigo` writes a `coding_requests` row; the bot runs the existing `RequestCoding` after the turn | Reuses the tested `/code` flow; path validation stays in the bot. B (server runs `claude` itself) and C (parse `/code` from text) rejected |
| Who | Owner, chat context only | Code changes are owner-only already |
| Execution | Unchanged: plan only; execution on «dale» | Human in the loop |
| Isolation | Planner and executor add `--strict-mcp-config`, `--setting-sources project` | No host connectors / project `.mcp.json`; target project's CLAUDE.md and project settings (`.claude/settings.json`) still apply, but `.claude/settings.local.json` and user-level settings don't |

## 3. Tool

`proponer_codigo(instruccion: str, carpeta: str | None = None) -> str`

- Permission: `allowed_ari_tools(is_owner=True, CHAT)` only (added to `ARI_TOOLS`; not in
  user chat, task or heartbeat).
- Validation: `instruccion` stripped, 1–2000 chars; `carpeta` optional, ≤ 500 chars
  (resolved and checked against the allowed root by the bot, not the server).
- Effect: insert into `coding_requests`; receipt `🛠️ Preparando plan: <instrucción>`
  (instruction truncated to 120 chars in the receipt).
- Tool description (model-facing): use it only when the creator asks to implement/change
  code, or accepted Ari's suggestion to do so; it only prepares a plan; execution needs
  the creator's «dale».

## 4. Data model

```sql
CREATE TABLE IF NOT EXISTS coding_requests (
  id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, chat_id TEXT NOT NULL,
  instruction TEXT NOT NULL, target TEXT, status TEXT NOT NULL,
  created_at TEXT NOT NULL, detail TEXT);
```
Statuses: `pending` → `taken` → `done` | `failed` | `skipped`. `claim_pending()` uses a
single guarded `UPDATE … SET status='taken' WHERE status='pending' … RETURNING` (same
cross-process pattern as `claim_due`).

## 5. Bot side (`CodingRequestRunner`)

Runs from the `after_turn` hook (with the outbox flush) and on every scheduler tick:

1. Claim pending requests.
2. Request older than 1 hour → `skipped`, notify "Descarté una propuesta de código vieja:
   <instrucción>".
3. If the owner has a coding job running or a plan awaiting «dale» (`PendingStore`) →
   `skipped`, notify "No preparé «<instrucción>»: ya tengo un trabajo o plan de código
   pendiente; respóndelo primero." (instruction truncated to 120 chars). `route_message`'s
   `/code` branch refuses with the same text when a background plan is being prepared
   (`PendingStore.is_planning`).
4. Otherwise run `RequestCoding(user_id, instruction, target, proposed=True)` **in the
   background** (anti-GC task set), inside a progress message for the owner's chat; send
   its reply (the plan + "Responde dale…", or the out-of-root refusal) to the chat; mark
   `done`, or `failed` with detail and "No pude preparar el plan: <motivo>" on exception.
   `proposed=True` marks the stored `PendingAction` so only an exact «dale» confirms it.
5. The owner's «no» always cancels. For a proposed plan, only an exact «dale» confirms it;
   any other affirmative (sí, ok…) falls through to chat, same as an unrelated reply.
   `/code` plans keep the existing looser affirmative set unchanged.
6. On startup, before the scheduler starts, `SqliteCodingRequests.reset_taken()` marks any
   row left `taken` by a crash/restart as `failed` ("interrumpido") and the bot notifies
   each chat: "Se interrumpió la preparación del plan: <instrucción>".

Planning uses the existing coder timeout (`ARI_CODING_TIMEOUT_SECONDS`).

## 6. `/code` isolation

`ClaudeCodeCoder` plan and exec argv add `--strict-mcp-config` and
`--setting-sources project` (keeping existing permission modes/allowed tools and
`cli_env`). The target repo's project settings (`.claude/settings.json`) and
CLAUDE.md still apply; `.claude/settings.local.json` and user-level settings do not.

## 7. Other changes

- Capabilities: "Puedes proponer y preparar cambios de código con proponer_codigo; se
  ejecutan solo con el «dale» de tu creador." (owner-only) and the context-aware tools
  hint lists `proponer_codigo` for owner chat.
- Fix the leftover ruff I001 in `src/ari/mcp_server/__main__.py`.

## 8. Security

- Only the owner's chat can create requests (permission table + server re-check +
  `--allowed-tools`); tasks/heartbeat/non-owners cannot.
- Proposing only plans (plan permission mode, no edits); execution requires an explicit
  «dale» in chat.
- Injected content asking to "implement X" can at worst produce a plan the owner
  discards; prompt rule "admin/… only when your creator asked" extended to
  `proponer_codigo`.

## 9. Testing

Fast: permission matrix incl. `proponer_codigo`; tool validation and receipt;
`coding_requests` store (add, claim once under concurrency, statuses); runner: calls
RequestCoding, respects pending job/plan, stale skip, out-of-root reply, failure reply,
background execution doesn't block the turn; coder argv contains the isolation flags.
Slow (real CLI): owner turn "implementa sum.py…" → `mcp__ari__proponer_codigo` called, a
`coding_requests` row exists, repo unchanged.

## 10. Acceptance (Telegram)

1. "agrega un comando /ping que responda pong" → receipt, then the plan.
2. «dale» → branch + commit, as `/code`.
3. A non-owner cannot trigger plans; 4. a scheduled task cannot either.

## 11. Out of scope

Heartbeat-originated code suggestions; auto-execution; multiple concurrent plans per owner.
