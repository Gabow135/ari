# Cross-user grants: sharing capabilities between Telegram users

- **Date**: 2026-10-03
- **Status**: Design — awaiting review
- **Author**: brainstorming session with the owner

## Intent

Today every user's data in Ari is strictly private, keyed by their Telegram
`user_id`. The owner wants a user (A) to be able to grant another user (B)
access to a **specific capability** of A's — reminders being the first
concrete case ("let B see/manage the same reminders I have").

The agreed shape is a **general, reusable grant model** — A grants B access to
capability X — implemented first for the `schedule` capability
(reminders + tasks), and designed so new capabilities can be added later.

### Decisions locked in during brainstorming

| Question | Decision |
|----------|----------|
| Scope | General permission system (`grants`), not a one-off for reminders. |
| Permission level | **Read + act**: B can view *and* act on behalf of A. |
| How B targets A | **Per command**, in natural language ("Ari, mostrame los recordatorios de @A", "creá uno en lo de @A"). No sticky "act-as" mode. |
| Identity | **By `@username`**, resolved to a `user_id`. B (grantee) and A (grantor) must be known/approved users of the bot. |
| Enforcement location | **In the Ari MCP server**: the tool receives an optional target, resolves it, checks the grant, and only then touches A's data. The real actor stays B → accountability is preserved. |
| On-behalf scheduling delivery | **Deliver to A's `user_id` as the chat**, matching how `enviar_mensaje` already reaches a user (`outbox_add(rec.user_id, …)`). Ari is a per-user DM assistant, so `chat_id == user_id` for every approved user — no separate chat table is needed. |
| Acceptance | B does **not** need to accept (A risks A's own data), but B **is notified**. |
| Granularity | Per capability (all of A's reminders), **not** per item. |
| v1 capabilities | `schedule` only. Email and other MCP-backed capabilities are out of scope (see below). |

## Why email is out of scope for v1

The general model fits cleanly for **per-user data** (reminders/tasks live in
`schedules` keyed by `user_id`; user "datos" live in `facts` the same way).
Email, however, is **not per-user data** — it is a shared MCP connection gated
by role in `servers.json`. "Acting as A for email" would not share A's data;
it would just use a shared connection. So email does not map onto the
grant-a-capability model and is deliberately excluded. `facts` ("datos") is a
trivial future second capability but is also out of scope for v1 to keep the
first slice small.

## Architecture

### Data model (new table in `db.py` `_SCHEMA`)

The table is added with `CREATE TABLE IF NOT EXISTS`, so it migrates
automatically on the next `connect`/`open_existing` (the schema is idempotent
and run by both the main process and the MCP server process).

```sql
CREATE TABLE IF NOT EXISTS grants (
  grantor_id TEXT NOT NULL,     -- owner of the data
  grantee_id TEXT NOT NULL,     -- who receives access
  capability TEXT NOT NULL,     -- v1: 'schedule'
  level      TEXT NOT NULL,     -- 'read' | 'act'
  created_at TEXT NOT NULL,
  PRIMARY KEY (grantor_id, grantee_id, capability));
```

`grants` PK is `(grantor_id, grantee_id, capability)` — one grant per
pair+capability; re-granting upserts the `level`. `level` is ordered
`read < act`: an `act` grant satisfies a `read` requirement.

On-behalf scheduling and the `compartir` notification both deliver to the
target user's `user_id` as the chat destination (via `turn_log.outbox_add`),
matching the existing `enviar_mensaje` pattern. No separate chat table is
required.

### New domain + infrastructure

- `domain/grants/entities.py` — `Grant` dataclass; `READ`/`ACT` level
  constants with an ordering helper (`level_at_least(have, need)`).
- `domain/grants/grants_port.py` — `GrantPort` protocol (`upsert`, `get`,
  `list_granted_by`, `list_granted_to`, `revoke`, `delete_for_user`).
- `infrastructure/grants/sqlite_grant_store.py` — `SqliteGrantStore`
  implementing the port against the `grants` table (same `asyncio.Lock`
  write pattern as the other stores).
- `application/grants/grant_policy.py` — `GrantPolicy.allows(grantee_id,
  grantor_id, capability, min_level) -> bool`. The single decision point.

No new chat store is needed: on-behalf delivery uses the target's `user_id`
(see Data model).

### Identity resolution

Reuse the existing `AriTools._resolve(arg, {APPROVED})` (ari_tools.py), which
matches an `@username` or numeric id against approved `access` records via
`_match` and returns `(record, error_message)` — a clear error on miss or
ambiguity. No new store method is required. Only **approved** users resolve.
Owner edge case: an owner may not have an `access` row, so `@owner` may not
resolve — the existing error message ("No encontré a …") surfaces rather than
failing silently. (Resolving the owner as a grantor/grantee by `@` is a known
limitation noted here, not solved in v1.)

### Grant management tools (MCP, natural language)

Mirror the existing `aprobar_acceso`/`revocar_acceso` pattern — new tools on
the Ari MCP server, callable by any user over **their own** data (actor = A):

- `compartir(capacidad, usuario, nivel="act")` — upsert a grant from the actor
  to `usuario`. Notifies B. `capacidad` is a user-facing Spanish label
  (`"recordatorios"`/`"tareas"`/`"agenda"`) mapped to the internal capability
  key `schedule` (same label→key pattern as `_KINDS` in `ari_tools.py`); an
  unknown label returns a clear error listing the shareable capabilities.
- `ver_permisos()` — list grants the actor **gave** and grants the actor
  **received**.
- `revocar_permiso(capacidad, usuario)` — delete a grant the actor gave.

These are sensitive tools: add `compartir` and `revocar_permiso` to the
`_INJECTION_RULE` list in `schedule_actions.py` so Ari only acts on them when
the owner asks in their own message, never because some email/page said so.

### Targeting + enforcement (the core)

The user-scoped agenda tools gain an optional target parameter
`de_usuario: str | None = None` (an `@username` or id):

- `listar_agenda(de_usuario=None)`
- `agendar(tipo, texto, at, cron, de_usuario=None)`
- `cancelar(id, de_usuario=None)`

Behavior inside `AriTools`:

1. `de_usuario is None` → operate on `self._a.user_id` (**current behavior,
   untouched**).
2. Otherwise:
   - Resolve `@usuario` → `target_id` via the access store. Unresolvable →
     clear error, no data touched.
   - `GrantPolicy.allows(actor=self._a.user_id, grantor=target_id,
     capability='schedule', min_level)`:
     - `listar_agenda` requires `read`.
     - `agendar` / `cancelar` require `act`.
   - Denied → clear error ("No tenés permiso para … de @A"), no data touched.
   - Allowed → run against `target_id`:
     - `listar_agenda`: `list_for_user(target_id)`.
     - `cancelar`: `get(id)` then verify `item.user_id == target_id` (not the
       actor) before cancelling.
     - `agendar`: `count_active(target_id)` for the cap, and
       `add(target_id, target_id, …)` — the target's `user_id` is also the
       chat destination (DM assistant; matches `enviar_mensaje`), so A receives
       the reminder.

The actor in the per-turn env stays B, so `turn_log` receipts record that B
acted (on A's data). The LLM cannot forge the actor; it can only pass a target
`@username`, which is grant-checked server-side.

### Permissions registry

`domain/tools/ari_permissions.py`: register `compartir`, `ver_permisos`,
`revocar_permiso` in `ARI_TOOLS` and in `allowed_ari_tools` for the CHAT
context (both owner and non-owner — any user manages their own grants). The
new `de_usuario` parameter does not change which tools are *allowed*; the
read-only TASK/HEARTBEAT contexts keep denying the write tools as today.

### Revocation cascades

- Grants are checked **live** on every call, so revoking a grant takes effect
  immediately — no stored-permission cache to invalidate.
- Reminders B created on behalf of A remain A's data (correct — they are A's).
- When a user's **bot access** is revoked (`AccessGate._revoke` /
  `on_revoke`), also delete every grant where that user is grantor **or**
  grantee (`GrantPort.delete_for_user`). Wire this into the existing
  `on_revoke` hook alongside `schedule.cancel_user`.

## Data flow (example)

1. A: "Ari, compartí mis recordatorios con @B así los maneja."
   → `compartir("recordatorios", "@B", "act")` → upsert grant
   `(A, B, schedule, act)` → notify B.
2. B: "Ari, mostrame los recordatorios de @A."
   → `listar_agenda(de_usuario="@A")` → resolve @A → check `read` → list A's.
3. B: "Ari, creale a @A un recordatorio mañana 9am: pagar la luz."
   → `agendar("recordatorio", "pagar la luz", at=…, de_usuario="@A")`
   → check `act` → look up A's `last_chat_id` → add as A's item → A gets it.
4. A: "Ari, sacale el acceso a @B." → `revocar_permiso("recordatorios", "@B")`.

## Error handling

Every on-behalf failure returns a clear Spanish message and touches no data:
- `@usuario` not an approved user → "No encontré a @X entre los usuarios aprobados."
- No / insufficient grant → "No tenés permiso para {ver|gestionar} los recordatorios de @A."
- Cancel referencing an id that is not A's → "No encontré el #N entre los recordatorios de @A."

## Testing strategy (strict TDD — observe RED first)

- `SqliteGrantStore`: upsert/get/list/revoke/delete_for_user round-trips;
  re-grant updates level.
- `GrantPolicy.allows`: read-grant satisfies read not act; act-grant satisfies
  both; missing grant denies.
- `SqliteAccessStore.find_by_username`: resolves approved user, strips `@`,
  returns None for unknown/pending.
- `AriTools` on-behalf paths: read allowed with read grant; act denied without
  act grant; no grant denies; unresolvable target; on-behalf scheduling writes
  the item under the target's `user_id`/chat; cancel verifies ownership against
  target.
- Grant-management tools: `compartir` upserts + notifies; `ver_permisos`
  lists both directions; `revocar_permiso` deletes.
- `ari_permissions`: new tools allowed in CHAT, denied in TASK/HEARTBEAT.
- `_INJECTION_RULE` includes `compartir`/`revocar_permiso`.
- Access revocation deletes the user's grants (both directions).

## Out of scope (YAGNI)

- Email and other MCP-backed capabilities.
- `facts`/"datos" capability (trivial future add, same pattern).
- Explicit acceptance flow for B (notify only).
- Per-item grants.
- Resolving owners by `@username` when they have no `access` row.

## Open limitations noted

- `@owner` may be unresolvable as grantor/grantee (no `access` row).
- Delivery assumes `chat_id == user_id` (per-user DM assistant). If Ari ever
  supports group chats, on-behalf delivery would need a real chat lookup.
