# Ari personal WhatsApp (read + approved reply) — Design

- **Date:** 2026-10-05
- **Status:** Approved (conversational design) — pending written-spec review
- **Author:** brainstorming session (Gabriel + Ari assistant)
- **Topic:** Let Ari read the owner's **personal** WhatsApp, notify the owner on
  Telegram about filtered incoming messages, and send replies **only** to the
  messages the owner indicates, with an explicit confirmation before every send.

## 1. Context & problem

Ari is a Telegram assistant powered by Claude (the LLM is a `claude` CLI
subprocess, `ClaudeCodeCliAdapter`). It runs as a single long-lived process
(`ari`, Telegram long-polling via `python-telegram-bot`) wired on one asyncio
loop in `src/ari/main.py`. It exposes its own tools through an MCP server
(`src/ari/mcp_server/`, process `ari`) and talks to external services through
third-party MCP servers.

The gateway layer is channel-agnostic: `GatewayPort`
(`src/ari/domain/ports/gateway_port.py`) defines `IncomingMessage`,
`OutgoingMessage`, and a `Handler`; `TelegramAdapter` is the only adapter today.
The owner is identified by Telegram user ids in `settings.owner_ids`
(`owner_id_set`); in a Telegram private chat the `chat_id` equals the owner's
user id, so proactive sends (reminders, system notices, heartbeat) reach the
owner via `send(chat_id, text)` in `main.py`.

**The ask:** connect Ari to the owner's existing **personal** WhatsApp account so
it can read incoming messages and reply to the ones the owner tells it to.

**The hard reality (locked in brainstorming):** WhatsApp has **no official API**
for reading/replying to a user's own personal chats. The official WhatsApp
Business Cloud API only serves a *separate* business number and cannot touch
existing personal conversations. The only way to meet the ask is an **unofficial
multi-device library** that links as a companion device (the WhatsApp Web
pairing mechanism). This violates WhatsApp's Terms of Service and carries a
**real, user-accepted ban risk**. Ban risk tracks **sending behaviour**, not the
library choice, so the mitigation is behavioural and is designed into the system
(see §8), not bolted on.

## 2. Locked decisions (from brainstorming)

1. **Path:** full read **and** reply, with the user accepting the ToS/ban risk.
2. **Control channel:** **Telegram**. Ari notifies the owner and takes reply
   orders on the existing Telegram chat, reusing owner auth and the existing
   confirmation flow. No new control surface.
3. **Inbound behaviour:** **filtered**. Ari notifies the owner on Telegram only
   for allowlisted contacts and/or keyword matches; everything else is stored
   and queryable on demand, never a notification firehose.
4. **Drafting:** Ari **drafts** the reply from the owner's instruction; the owner
   approves before sending. (Dictating exact text word-for-word remains possible
   on request; the confirmation gate protects both modes.)
5. **Technology — Approach A:** **neonize** (`pip install neonize`), a Python
   binding to the Go **whatsmeow** multi-device library, running **in-process**
   in Ari's asyncio loop. Rejected: **B** Baileys (Node.js) — adds a second
   runtime + IPC bridge for the same protocol and same risk; **C**
   whatsapp-web.js (Puppeteer/headless Chrome) — heaviest, largest detection
   surface, strictly worse for this use. neonize is actively maintained (v0.5.2,
   Sep 2026), ships prebuilt wheels for Linux/macOS/Windows (x86_64 + ARM), and
   speaks the same protocol as Baileys.

## 3. Goals / non-goals

**Goals**

- Ari receives the owner's incoming WhatsApp messages in near-real-time while it
  is running, stores them, and surfaces a **filtered** subset to the owner on
  Telegram.
- The owner can ask "what came in?" and get a clean list from Ari.
- The owner can tell Ari to reply to a specific message; Ari drafts it, shows it,
  and sends it **only after an explicit confirmation**.
- All WhatsApp capability is **owner-only**; no other Ari user can read or send.
- Hexagonal: a domain port isolates neonize so it is swappable (Baileys later)
  without touching application logic. The LLM core, memory, and Telegram adapter
  are untouched.

**Non-goals (v1)**

- No auto-reply: Ari never answers a WhatsApp message on its own.
- No broadcast / bulk send: the send path targets exactly one chat per confirmed
  action. There is no mass-send tool at all.
- No group-chat sending in v1; group messages may be ingested (read) but replies
  target 1:1 chats. (Groups opt-in later.)
- No media send/receive processing beyond text in v1 (incoming media is recorded
  as a typed placeholder, e.g. "📎 image", not downloaded/processed yet).
- Not multi-account: exactly one WhatsApp session, the owner's.

## 4. Architecture (hexagonal) + the cross-process bridge

The single most important structural fact: **the neonize session lives in the
main `ari` process** (on the shared asyncio loop, next to the Telegram bot),
**but the MCP tools run in the `ari` subprocess**. A tool therefore cannot call
`neonize.send()` directly. The design bridges the two processes through the
**shared SQLite database** (`ari.db`, already opened by both via
`ARI_DB_PATH`), reusing Ari's existing **outbox** pattern:

- **Main process** owns the live session: receives inbound events, runs ingest,
  notifies on Telegram, and **flushes** queued outbound replies through neonize.
- **`ari` MCP subprocess** only ever **reads** pending messages and **enqueues**
  outbound replies into the shared DB. It never touches the socket.

This mirrors `src/ari/application/outbox.py` (`nn`), which already runs after
every turn and in the `Scheduler` to perform sends the subprocess could not.

### 4.1 Domain — `src/ari/domain/whatsapp/`

- `whatsapp_port.py` — `WhatsAppPort` (Protocol):
  - `async start(on_message: Callable[[InboundWhatsApp], Awaitable[None]]) -> None`
  - `async send(wa_chat_id: str, text: str) -> None`
  - `async pair_phone(number: str) -> str` (returns the 8-char link code)
  - `connection_state() -> str` (`connected` / `reconnecting` / `logged_out`)
- `entities.py` — frozen dataclasses:
  - `InboundWhatsApp(wa_chat_id, contact_name, text, media_kind, ts, is_group)`
  - `PendingReply(wa_chat_id, contact_name, draft_text)` (the confirm payload)

### 4.2 Infra adapter — `src/ari/infrastructure/whatsapp/neonize_adapter.py`

- `NeonizeWhatsApp(WhatsAppPort)` — thin wrapper over neonize. Registers the
  neonize message event, adapts each event to `InboundWhatsApp`, and forwards it
  to the injected `on_message` coroutine. Exposes `send`, `pair_phone`,
  `connection_state`. **All logic stays out of this class** so everything else is
  testable without WhatsApp.
- neonize session state is persisted by whatsmeow in its own SQLite store under
  `~/.ari/whatsapp/` (outside the repo, like the vault and workspaces). Survives
  restarts; no re-pairing unless the device is revoked from the phone.
- Auto-reconnect with bounded backoff; surfaces state changes to the main
  process so a prolonged disconnect becomes a Telegram notice.

### 4.3 Infra persistence — `src/ari/infrastructure/persistence/sqlite_whatsapp.py`

`SqliteWhatsApp` on the shared `conn` (tables created in the same `connect()`
schema path as the rest). Two concerns:

- **Messages** `whatsapp_messages`: `id, wa_chat_id, contact_name, direction
  ('in'|'out'), text, media_kind, ts, status ('pending'|'notified'|'queued'|
  'sent'|'failed')`.
- **Filter config** `whatsapp_filter`: allowlisted contacts and keywords
  (owner-scoped; single owner session). Read by ingest, edited by the
  `whatsapp_filtro` tool.

Methods: `record_inbound`, `mark_notified`, `list_pending`, `enqueue_outbound`,
`claim_queued`, `mark_sent`, `mark_failed`, `get_filter`, `set_filter`. The
`claim_queued`/`mark_sent` transitions must be atomic (same discipline as
`SqliteScheduleStore.claim_due`) so the flusher never double-sends.

### 4.4 Application — ingest, filter, notify

`src/ari/application/whatsapp/ingest.py` — `WhatsAppIngest`, constructed in the
**main** process and registered as the adapter's `on_message`:

1. `record_inbound(...)` → status `pending`.
2. Apply the filter (`get_filter`): contact in allowlist **or** text matches a
   keyword.
3. If it passes → `notify(owner_chat, "📱 WhatsApp de <contacto>: <texto>")`
   using the existing proactive `send`, then `mark_notified`. Notifies every id
   in `owner_id_set`.
4. If not → leave it `pending` (queryable on demand).

Pure filter logic lives in a small helper (`filter.py`) so it is unit-tested in
isolation.

### 4.5 Application — outbound flush

`src/ari/application/whatsapp/outbox.py` — `WhatsAppOutbox`, a coroutine added to
the `Scheduler` list in `main.py` (next to the existing `nn` outbox). Each tick:
`claim_queued()` → `port.send(wa_chat_id, text)` with the human-pace delay (§8)
→ `mark_sent` (or `mark_failed` + a Telegram notice to the owner). Best-effort,
never raises into the loop.

### 4.6 Confirmation (reuse existing pattern)

Sending is a **propose → confirm → execute** action, modelled on
`proponer_comando` (`src/ari/application/coding/`): the `whatsapp_responder` tool
stores a `PendingReply` in a pending slot and returns the draft for the owner to
see; the next affirmative owner message ("dale"/"sí", via the existing
`is_affirmative`/`is_dale` logic) is what actually `enqueue_outbound`s it. No
confirmation ⇒ nothing is enqueued. This makes the gate real machinery, not LLM
discipline.

### 4.7 MCP tools — `src/ari/mcp_server/server.py` + `application/ari_tools.py`

Three `@tool` wrappers (Spanish model-facing docstrings, consistent with the
existing tools), delegating to `AriTools`, each gated by `self._allowed(...)`:

- `whatsapp_pendientes(limite=10)` — read `list_pending`, format a list.
- `whatsapp_responder(id, instruccion)` — resolve the pending message, draft the
  reply text from the instruction, and open the confirmation (§4.6).
- `whatsapp_filtro(accion, valor)` — manage the allowlist/keywords
  (`agregar_contacto` / `quitar_contacto` / `agregar_palabra` / `ver`).

### 4.8 Wiring — `src/ari/main.py` and `config/settings.py`

- `build(...)` constructs `NeonizeWhatsApp`, `SqliteWhatsApp`, `WhatsAppIngest`,
  `WhatsAppOutbox` when WhatsApp is enabled.
- After the bot starts: `await whatsapp.start(ingest)`; add `whatsapp_outbox`
  to the `Scheduler`; on first run with no session, drive pairing (§7).
- New settings (all `ARI_`-prefixed, WhatsApp **disabled by default**):
  `whatsapp_enabled: bool = False`, `whatsapp_number: str = ""` (for pairing
  code), `whatsapp_session_dir: str = "~/.ari/whatsapp"`,
  `whatsapp_send_min_delay_seconds: int = 3`.

## 5. Tool contracts & behaviour

### 5.1 `whatsapp_pendientes(limite=10) -> str`

Lists unanswered inbound messages, newest first:
`#<id> · <contacto> · <hora> · <texto|📎 media>`, or "no hay WhatsApp
pendientes". `limite` clamped to a sane max (e.g. 25).

### 5.2 `whatsapp_responder(id, instruccion) -> str`

- Resolves message `#id` from the store; error if unknown/already answered.
- Drafts the reply text from `instruccion` (Ari composes it).
- Opens the confirmation and returns, e.g.: *"Voy a responder a <contacto>:
  «<draft>». ¿Confirmo?"* Nothing is sent yet.
- On the owner's next affirmative turn, the pending reply is `enqueue_outbound`ed
  and the `WhatsAppOutbox` flush sends it; Ari confirms *"✅ Enviado a
  <contacto>"*. On send failure: *"❌ No pude enviar a <contacto>"*, status
  `failed`, message stays unanswered.

### 5.3 `whatsapp_filtro(accion, valor="") -> str`

`agregar_contacto <nombre|numero>`, `quitar_contacto <…>`, `agregar_palabra
<keyword>`, `quitar_palabra <…>`, `ver`. Returns the resulting filter.

## 6. Permissions (owner-only)

- Add `whatsapp_pendientes`, `whatsapp_responder`, `whatsapp_filtro` to
  `ARI_TOOLS` in `src/ari/domain/tools/ari_permissions.py`.
- **Do NOT** add them to `_USER_CHAT` — that is the owner-only mechanism
  (`allowed_ari_tools` gives non-owners only `_USER_CHAT` in CHAT).
- Absent from `TASK` and `HEARTBEAT` (those stay `_READ`-only): nothing
  autonomous can read or send WhatsApp.

## 7. Pairing (one-time, headless-friendly)

Ari is headless, so a scanned QR is awkward. Use whatsmeow's **phone-number
pairing code**: with `whatsapp_number` set, on first run (no persisted session)
Ari calls `pair_phone(number)`, receives an 8-char code, and sends it to the
owner on Telegram: *"Vinculá Ari: WhatsApp → Dispositivos vinculados → Vincular
con número de teléfono → ingresá: ABCD-1234"*. Fallback: render the QR to a PNG
and `send_document` it to the owner. Once paired, the session persists in
`~/.ari/whatsapp/`.

## 8. Ban-risk mitigation (designed in, not optional)

- **Reply-only to known contacts**: no cold-starting conversations; v1 replies
  target chats that already messaged the owner.
- **Human pace**: `whatsapp_send_min_delay_seconds` enforced in the flusher; one
  message per confirmed action; no bursts.
- **No broadcast/bulk tool** exists in the surface at all.
- **Explicit confirmation** before every send (§4.6).
- **Owner-only** end to end (§6).
- The owner was told, and accepted, that misuse still risks a ban; the design
  minimises the detectable automation signature but cannot eliminate the risk.

## 9. Resilience & failure handling

- **Disconnect** → auto-reconnect with backoff; a prolonged outage sends the
  owner a Telegram notice; recovery is silent.
- **Ari stopped** → messages queue on WhatsApp (multi-device tolerates ~14 days
  phone-offline) and sync on reconnect; no real-time notice while stopped.
- **neonize/Go core crash** → contained in the adapter; the Telegram chat and the
  rest of Ari keep working.
- **Send failure** → `mark_failed` + owner notice; never silently dropped, never
  marked sent.
- **WhatsApp disabled** (`whatsapp_enabled=False`, the default) → none of the
  wiring starts and the three tools no-op with a clear message.
- **Logged out from the phone** → `connection_state()=logged_out` → owner notice
  prompting re-pair; no crash loop.

## 10. Testing plan (strict TDD)

- `filter.py`: allowlist match, keyword match (case/accents), no-match. RED→GREEN.
- `WhatsAppIngest` (fake `WhatsAppPort` + fake store + fake `send`): records,
  notifies on pass, stays silent on fail, notifies each owner id.
- `SqliteWhatsApp`: inbound record; status transitions
  `pending→notified→queued→sent/failed`; atomic `claim_queued` (no double-send);
  filter get/set.
- `WhatsAppOutbox`: claims queued, sends via fake port, marks sent; failure →
  `failed` + notice; respects min delay.
- Confirmation: `whatsapp_responder` opens a pending reply and sends **nothing**;
  an affirmative turn enqueues exactly once; a non-affirmative turn does not.
- MCP tools: owner gating (a non-owner is denied), formatting, unknown/answered
  id handling.
- `ari_permissions`: the three tools present for owner CHAT, **absent** from
  `_USER_CHAT`, `TASK`, and `HEARTBEAT`.
- The `NeonizeWhatsApp` adapter itself is kept thin and validated manually during
  real pairing (not unit-tested against the live protocol).

## 11. Files touched

New:

- `src/ari/domain/whatsapp/whatsapp_port.py`
- `src/ari/domain/whatsapp/entities.py`
- `src/ari/infrastructure/whatsapp/neonize_adapter.py`
- `src/ari/infrastructure/persistence/sqlite_whatsapp.py`
- `src/ari/application/whatsapp/ingest.py`
- `src/ari/application/whatsapp/filter.py`
- `src/ari/application/whatsapp/outbox.py`

Changed:

- `src/ari/infrastructure/persistence/db.py` — add the two WhatsApp tables to the
  schema.
- `src/ari/application/ari_tools.py` — `whatsapp_pendientes`,
  `whatsapp_responder`, `whatsapp_filtro`.
- `src/ari/mcp_server/server.py` — three `@tool` wrappers.
- `src/ari/mcp_server/__main__.py` — build the store + tools from env if needed.
- `src/ari/domain/tools/ari_permissions.py` — register the three tools (owner-only).
- `src/ari/main.py` — construct/wire the session, ingest, and outbox flusher;
  pairing on first run.
- `src/ari/config/settings.py` — the four new settings.
- `pyproject.toml` — add `neonize` dependency (consider an optional extra, like
  `diarization`, so the base install stays lean).
- Tests under `tests/` mirroring the above.

## 12. Open questions

None blocking. Two deferred: (a) group-chat replies and media send/receive are
explicit v1 non-goals, addable later behind the same port; (b) the filter config
is currently single-owner global — if multi-owner notification ever needs
per-owner filters, the `whatsapp_filter` table gains an owner column without
changing the architecture.
