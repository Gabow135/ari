# Ari — Proactivity: reminders, recurring tasks, heartbeat, system notices (Design)

- **Date:** 2026-09-25
- **Status:** Draft — pending user approval
- **Builds on:** soul & capabilities (`soul/SOUL.md`, `src/ari/domain/agent/capabilities.py`),
  access gate, progress/streaming, lifecycle (`/stop`, `/restart`)
- **Scope:** Ari stops being purely reactive: it keeps scheduled reminders and
  recurring tasks created in natural language, takes initiative on an hourly
  heartbeat (owner only), and pushes system notices (owner only).

## 1. Purpose

Today Ari only speaks when spoken to. After this change:

- A user writes *"recuérdame mañana a las 9 llamar a Juan"* and at 09:00 Ari
  sends *"⏰ Recordatorio: llamar a Juan"*.
- A user writes *"cada lunes a las 8 resúmeme mis pendientes"* and every Monday
  at 08:00 Ari runs that instruction as a normal turn and sends the result.
- Every hour (outside quiet hours) Ari reviews `soul/HEARTBEAT.md`, the owner's
  memory and upcoming items, and messages the owner only if it has something
  worthwhile (usually it stays silent).
- The owner gets direct notices for operational events (stale access requests,
  Claude CLI failing, paused tasks).

Success: all four behaviors work, survive `/restart` and outages, never
double-fire, and never spam.

## 2. Locked decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| NL → schedule | **A1: action block** in the same chat reply (`<ari-action>{json}</ari-action>`), parsed, validated and stripped by the app | One Claude call, no added latency. A2 (classifier call) doubles cost; A3 (internal MCP tools) deferred to the autonomy phase, which will migrate A1 onto it |
| Engine | **B1: in-process asyncio loop + SQLite `schedules` table** | Persistent across restarts, cross-platform, no heavy deps. B2 (PTB JobQueue) is memory-only; B3 (OS cron) is per-platform |
| Recurrence | 5-field cron evaluated with `croniter` in the configured timezone | Small, stable dependency; exact semantics |
| Who | Reminders & tasks: **owner and approved users**, each their own. Heartbeat & system notices: **owner only** | User chose "Mixto" |
| Creation UX | **Natural language only**; `/recordatorios` lists; cancel by saying so | User choice |
| Timezone | `America/Guayaquil`; quiet hours **22:00–07:00** | User choice |
| Heartbeat | Every **60 min**; `0` disables | User choice |

## 3. Architecture

Additive, hexagonal, same patterns as the access gate.

```
domain/schedule/
  entities.py        ScheduleItem, kinds, statuses
  actions.py         Action dataclasses + validation rules (pure)
  quiet_hours.py     is_quiet(now, tz, window) / next_quiet_end (pure)
application/schedule/
  action_parser.py   extract <ari-action> blocks → validated actions + clean text
  run_due_items.py   fire due reminders/tasks, compute next runs, recovery
  heartbeat.py       hourly initiative turn for the owner
  system_notices.py  event detection + quiet-hours queue
  scheduler.py       the loop: every 30 s calls the three above (injectable clock)
infrastructure/schedule/
  sqlite_schedule_store.py   SchedulePort over the `schedules` table (+ `kv` state)
soul/HEARTBEAT.md            editable checklist for the heartbeat
```

`main.py` starts the `Scheduler` in `post_init` and stops it in `post_shutdown`
(so `/stop` and `/restart` shut it down cleanly). Outbound messages use the same
best-effort `send(chat_id, text)` already used by access notices.

## 4. Data model

New table (`CREATE TABLE IF NOT EXISTS`, no migration):

| column | notes |
|---|---|
| `id` INTEGER PK | shown to users as `#12` |
| `user_id`, `chat_id` TEXT | owner of the item / delivery target |
| `kind` TEXT | `reminder` (fixed text) · `task` (Ari runs the instruction) |
| `text` TEXT | reminder text or task instruction |
| `next_run_at` TEXT | ISO-8601 **UTC** |
| `cron` TEXT NULL | NULL = one-shot; else 5-field cron in `ARI_TIMEZONE` |
| `status` TEXT | `active` · `running` · `done` · `cancelled` · `paused` |
| `failures` INTEGER | consecutive failures |
| `created_at`, `last_run_at` TEXT | audit, UTC |

Small key/value table `kv(key PRIMARY KEY, value)` for engine state:
`heartbeat.last_at`, `heartbeat.sent:<YYYY-MM-DD>`, `notices.queue`,
`notices.cli_down`, `notices.access_notified:<user_id>`.

All times stored in UTC; converted to `ARI_TIMEZONE` only to display and to
evaluate cron.

## 5. Engine (`Scheduler`)

- Ticks every **30 s** (configurable for tests; clock injected).
- **Claim before run:** in one transaction, due items (`status='active' AND
  next_run_at <= now`) are flipped to `running`. A crash mid-run leaves them
  `running`; on startup, `running` items are reset to `active` (at-least-once
  across crashes, never twice in normal operation).
- After running: one-shot → `done`; recurring → next `next_run_at` from
  `croniter` in the local timezone, `status='active'`, `failures=0`.
- **Recovery after downtime** (item due more than one tick ago):
  - reminder late < 1 h → sent with suffix *"(con retraso)"*;
  - reminder late ≥ 1 h → *"⚠️ Mientras estuve apagada no pude recordarte: …"*;
  - recurring task with several missed occurrences → run **once**, then
    schedule the next future occurrence.
- **Error isolation:** each item runs in its own try/except. A failure
  increments `failures` and retries next tick-cycle (item back to `active`,
  `next_run_at` + 5 min); at **3** consecutive failures → `paused` + system notice.
- **Per-user cap:** 20 active items; creation beyond that is rejected with a message.
- Items of a user whose access is revoked are cancelled (hook in `AccessGate._revoke`).

## 6. Reminders & tasks in natural language

**Prompt additions** (built by `AgentService` from data passed by `HandleMessage`):
current local datetime + timezone; the user's active items with `#id`; the
action format and rules below.

**Actions** (zero or more per reply):
```
<ari-action>{"type":"reminder","at":"2026-09-26T09:00-05:00","text":"llamar a Juan"}</ari-action>
<ari-action>{"type":"reminder","cron":"0 8 * * *","text":"tomar la pastilla"}</ari-action>
<ari-action>{"type":"task","cron":"0 8 * * 1","text":"resúmeme mis pendientes"}</ari-action>
<ari-action>{"type":"cancel","id":12}</ari-action>
```

**Validation** (domain, pure): valid JSON; exactly one of `at`/`cron` for
`reminder`/`task`; `at` has an offset, is in the future and ≤ 1 year ahead;
`cron` parses and its minimum interval is **≥ 1 hour**; `text` 1–500 chars;
`cancel.id` belongs to the same user and is active; user under the cap.

**Processing:** `ActionParser` removes every block from the reply text; valid
actions are applied; the app appends **code-generated** confirmations so the
user sees exactly what was stored:
`✅ Agendado #12: llamar a Juan — vie 26/09 09:00` ·
`🔁 Agendado #13 (cada lunes 08:00): resúmeme mis pendientes` ·
`🗑️ Cancelado #12`. Invalid blocks store nothing and append
`⚠️ No pude agendarlo: <razón>`.

**Blocks are honored only in replies to the user's own messages.** In task runs
and heartbeat replies they are stripped and ignored (no self-scheduling loops).

**`/recordatorios`** lists active items (`#12 · vie 26/09 09:00 · llamar a Juan`,
recurring shown as `cada lunes 08:00`). Available to owner and approved users.

**Firing:**
- reminder → `⏰ Recordatorio: <text>` sent directly (no Claude).
- task → a normal Ari turn (`HandleMessage`, same memory and role) with the
  instruction as the user message; reply sent as `🔁 Tarea #<id>: <reply>`;
  both stored in the user's history.

## 7. Heartbeat (owner only)

- Runs every `ARI_HEARTBEAT_MINUTES` (default 60; `0` disables), never during
  quiet hours. First beat one interval after startup; `heartbeat.last_at` is
  persisted so restarts don't trigger early beats.
- Turn input: soul + `soul/HEARTBEAT.md` (hot-reloaded like `SOUL.md`) + the
  owner's facts, summary and recent messages + items due in the next 24 h +
  pending system notices + current time + Ari's recent proactive messages.
- Output contract: exactly `NADA` → send nothing (expected most of the time);
  otherwise the text is sent prefixed with `💡` and stored in the history.
- Max **3** proactive messages per local day; told not to repeat topics it
  already raised. Action blocks ignored.
- Errors are logged and retried next beat; they feed the CLI-failure notice.

## 8. System notices (owner only, code-generated, no Claude)

| Event | Notice |
|---|---|
| Access request pending > 12 h | `🔔 @juan (id …) sigue esperando acceso. /aprobar K7QM-X3PA` — once per request |
| Claude CLI fails 3 times in a row (any caller) | `⚠️ Estoy teniendo problemas con Claude: «…». Revisa el token o la conexión.` — once; then `✅ Claude volvió a responder` on recovery |
| Task paused after 3 failures | `⏸️ Pausé la tarea #12 (…) porque falló 3 veces: …` |

CLI health is tracked by a small wrapper around the `LLMPort` that counts
consecutive failures/successes.

**Quiet hours:** heartbeat doesn't run; notices are queued (`notices.queue`) and
sent together at the end of the window. Reminders, tasks and replies to user
messages are **not** affected.

## 9. Capabilities registry

Add: reminders (one-shot & recurring), recurring tasks, `/recordatorios` (all
approved users), heartbeat & system notices (owner only). Remove from
`LIMITATIONS`: "no puedes escribir por iniciativa propia" and "no puedes programar
recordatorios ni tareas periódicas".

## 10. Configuration (additions)

| Variable | Default | Purpose |
|---|---|---|
| `ARI_TIMEZONE` | `America/Guayaquil` | Local time for display, cron and quiet hours |
| `ARI_QUIET_HOURS` | `22-7` | Quiet window (local, start-end hours); empty = none |
| `ARI_HEARTBEAT_MINUTES` | `60` | Heartbeat interval; `0` disables |
| `ARI_MAX_ITEMS_PER_USER` | `20` | Active reminders/tasks per user |

New dependencies: `croniter`, `tzdata` (IANA zones on Windows for `zoneinfo`).

## 11. Error handling

- Invalid action → nothing stored, user told why.
- Item execution error → isolated, retried, paused at 3, owner notified.
- Telegram send failure → logged; reminder counts as fired (no retry storm).
- Store errors in the loop → logged; the loop keeps ticking.
- Scheduler never blocks message handling (runs as its own task).

## 12. Testing (TDD)

All with a fake clock and fake store/LLM/sender; no real sleeps.
- Domain: action validation (each rule), quiet hours (window crossing midnight),
  cron minimum interval.
- `ActionParser`: extraction/stripping, multiple blocks, malformed JSON, foreign
  `#id`, ignored in non-user contexts.
- Engine: due selection, claim prevents double fire, `running` reset on start,
  recurring next-run in local tz, downtime recovery (late/very late/multiple
  missed), 3-failure pause, per-user cap, revoke cancels items.
- Heartbeat: `NADA` sends nothing, daily cap, quiet hours, interval persisted.
- Notices: 12 h threshold once, CLI down/up once each, quiet-hours queue flush.
- SQLite store: round-trips, claim transaction.
- Slow (real CLI): *"recuérdame en 2 minutos probar Ari"* yields a valid block.

## 13. Out of scope (later)

- Internal MCP tool server (autonomy phase; will replace action blocks).
- Proactive messages to non-owner users (heartbeat per user).
- Snooze/edit of reminders (cancel + recreate for now).
- Multi-instance deployments (single bot process assumed).

## 14. Acceptance criteria

1. "recuérdame en 2 minutos X" → confirmation with `#id`; message arrives ~2 min later.
2. "cada lunes a las 8 …" → runs Mondays 08:00 local; result stored in history.
3. `/recordatorios` lists; "cancela el 12" cancels.
4. `/restart` during a pending reminder → fires once after restart (late note if applicable).
5. Heartbeat never sends during 22–07; ≤ 3/day; `NADA` sends nothing.
6. CLI failures ×3 → one notice; recovery → one notice.
7. Non-owner users never receive heartbeat or system notices.
