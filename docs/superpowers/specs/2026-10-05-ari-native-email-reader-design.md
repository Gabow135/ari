# Ari native email reader (large emails + workspace overflow) — Design

- **Date:** 2026-10-05
- **Status:** Approved (conversational design) — pending written-spec review
- **Author:** brainstorming session (Gabriel + Ari assistant)
- **Topic:** A native, in-process IMAP reader so Ari can reliably read **large**
  emails (body and attachments), spilling oversized content to the per-user
  workspace where existing tools (`leer_documento`, `leer_archivo`,
  `consultar_sql`) can process it.

## 1. Context & problem

Ari is a Telegram assistant powered by Claude (the LLM is a `claude` CLI
subprocess, `ClaudeCodeCliAdapter`). It exposes its own tools through a small
MCP server (`src/ari/mcp_server/server.py`, process `ari`) and reaches external
services through third-party MCP servers listed in `mcp/servers.json`.

**Ari does not read email itself.** Every read path goes through the external
`mcp-mail-server@2.1.0` package:

- **Owner** → `email_corp` / `email_gmail` / `email_hotmail`, with IMAP/SMTP
  credentials substituted from `.env` by `McpRegistry`.
- **BYO users** → `ToolPolicy._inject_email()` decrypts the user's mailbox from
  `SqliteEmailAccounts` and spawns a per-turn `mail_<label>` instance via
  `email_server_spec()` (`src/ari/infrastructure/email/server_spec.py`).

When an invoice/payment email has a large body (full HTML), the mail MCP returns
it as one tool result. The `claude` CLI that acts as Ari's LLM truncates
oversized tool results, so the model never sees the total it needs to extract.
This is the observed failure ("el correo era muy grande, no pude extraer el
total del body" for the `mrjoy` and `NETLIFE` invoices). The limit is **not** in
Ari's own code, so raising an internal cap would not fix it.

## 2. Locked decisions (from brainstorming)

Three clarifying questions fixed the scope:

1. **Where the amount lives:** both — sometimes in the body, sometimes in an
   attached PDF/Excel. The reader must handle both.
2. **"Handle the filesystem well":** this is the **overflow buffer** for large
   emails, not a separate filesystem concern. No general file-handling rework.
3. **Audience:** owner **and** BYO users.

Chosen approach (**A** — native IMAP reader), over B (prompt-only guidance;
rejected because the CLI truncates the body before Ari could save it) and C
(full email subsystem with a dedicated MCP server, pagination, server-side
filters; rejected as over-engineering for today).

## 3. Goals / non-goals

**Goals**

- Ari reads a specific email and gets **clean, bounded** text regardless of size.
- Large bodies and all attachments are **spilled to the per-user workspace** so
  `leer_documento` (already extracts PDF/XLSX/CSV) and `leer_archivo` can process
  them on demand.
- Works for the owner's mailboxes and BYO users' mailboxes through **one** code
  path.
- No new third-party dependency.

**Non-goals (v1)**

- Not restricting `mcp-mail-server` to send-only (that is approach C). The mail
  MCP stays available; tool descriptions steer reading toward the native tools.
- No body pagination API, no server-side search filters beyond a small mapped
  set, no dedicated email MCP server.
- Email reading is exposed only in **CHAT** context. Background missions (`TASK`)
  and the heartbeat stay read-only-limited as today. (Noted as a future
  extension.)
- No sending from the native reader. Composing/sending stays on the mail MCP.

## 4. Architecture (hexagonal)

Follows the existing layering (domain port → infra adapter → application tool →
MCP wrapper), mirroring how `leer_documento` / `extract_document` is wired.

### 4.1 Domain — `src/ari/domain/email/`

- `email_reader_port.py` — `EmailReaderPort` (Protocol):
  - `search(spec: MailboxSpec, criteria: str, limit: int) -> list[EmailSummary]`
  - `fetch(spec: MailboxSpec, uid: str) -> FetchedEmail`
- New frozen entities in `src/ari/domain/email/entities.py` (alongside the
  existing `EmailAccount`):
  - `MailboxSpec(cuenta, imap_host, imap_port, imap_secure, user, password)` —
    the resolved connection info for one mailbox, keyed by the turn-visible
    account name (`cuenta`, e.g. `email_corp`, `mail_trabajo`).
  - `EmailSummary(uid, from_addr, subject, date, snippet)`
  - `EmailAttachment(filename, content_type, data: bytes)`
  - `FetchedEmail(uid, from_addr, to_addr, subject, date, body_text,
    attachments: list[EmailAttachment])`

### 4.2 Infra — `src/ari/infrastructure/email/imap_email_reader.py`

- `ImapEmailReader(EmailReaderPort)` using **stdlib only**: `imaplib`
  (`IMAP4_SSL` when `imap_secure`, else `IMAP4` + `starttls`), `email` for MIME
  parsing, `email.header` for subject/charset decoding.
- `search()` → `SELECT INBOX` (read-only `.select(readonly=True)`), build an IMAP
  `SEARCH` from `criteria` (see §5.1), fetch headers + a short snippet for the
  most recent `limit` UIDs, newest first.
- `fetch()` → `UID FETCH (RFC822)`, parse the MIME tree:
  - Body: prefer `text/plain`; otherwise convert the `text/html` part to text
    with a small `html.parser`-based stripper (new helper
    `src/ari/infrastructure/email/html_text.py`).
  - Attachments: every part with a filename (or `Content-Disposition:
    attachment`) → `EmailAttachment` with decoded bytes.
- Bounded resources: connection timeout (`socket`/`imaplib` timeout), close the
  connection in a `finally`.

### 4.3 Infra — workspace binary write

- `UserWorkspace.write_bytes(rel: str, data: bytes, *, max_bytes: int = 10_485_760)
  -> str` — new method. Today only `write_text` exists (1 MB text cap) and
  attachments are binary and can be larger. Same jail/`resolve` discipline;
  rejects over `max_bytes`; creates parent dirs; returns the workspace-relative
  path.

### 4.4 Application — `src/ari/application/ari_tools.py`

Two new async methods, gated by `_allowed(...)` like the rest:

- `buscar_correos(cuenta, criterio="", limite=10)`
- `leer_correo(cuenta, id)`

Both resolve the `MailboxSpec` from an in-memory lookup built at startup (see
§6), call the reader, and format the reply. `leer_correo` performs the workspace
spill (§5.3). Receipts via `self._receipt(...)` as other tools do.

### 4.5 MCP wrappers — `src/ari/mcp_server/server.py`

Two `@tool` wrappers with Spanish docstrings (model-facing copy, consistent with
the existing tools), delegating to `AriTools`. Descriptions explicitly say these
handle **large** emails and are the preferred way to read.

## 5. Tool contracts & behavior

### 5.1 `buscar_correos(cuenta, criterio="", limite=10) -> str`

- `cuenta`: the account name the model already sees in "Tus herramientas"
  (`email_corp`, `mail_trabajo`, …).
- `criterio`: free text mapped to IMAP `SEARCH`. A small, documented mapping:
  - empty → most recent `limite` (`ALL`, newest first)
  - `de:<addr>` → `FROM`
  - `asunto:<text>` → `SUBJECT`
  - `no leidos` / `nuevos` → `UNSEEN`
  - `desde:YYYY-MM-DD` → `SINCE`
  - anything else → `TEXT <criterio>` (server-side full-text)
- `limite`: clamped to a sane max (e.g. 25).
- Returns one line per message: `#<uid> · <de> · <asunto> · <fecha> · <snippet>`,
  or a clear "no encontré correos" / "no tenés esa casilla" message.

### 5.2 `leer_correo(cuenta, id) -> str`

Returns a structured, bounded text block:

```
De: … · Para: … · Fecha: …
Asunto: …

<body text, cleaned, truncated to ~8000 chars with a "… (truncado)" marker>

📄 Cuerpo completo: correos/<cuenta>-<uid>.txt
📎 Adjuntos (usa leer_documento sobre cada ruta):
- correos/adjuntos/<cuenta>-<uid>/<archivo-1>
- …
```

### 5.3 Workspace spill (the "filesystem" half)

On every `leer_correo`:

- Full cleaned body → `correos/<cuenta>-<uid>.txt` via `write_text` (if it
  exceeds the 1 MB text cap, truncate and note it).
- Each attachment → `correos/adjuntos/<cuenta>-<uid>/<safe-filename>` via the new
  `write_bytes`.
- Filenames sanitized to a basename run through the workspace's `_UNSAFE`
  sanitizer; de-duplicate collisions with a numeric suffix.
- Caps: per-attachment size cap (reuse `write_bytes` `max_bytes`) and a max
  attachment **count** (e.g. 20); skipped attachments are reported, never
  silently dropped.
- The inline reply stays bounded regardless of email size — that is the whole
  point.

## 6. Credential plumbing (no vault key for the `ari` server)

The `ari` MCP subprocess has **no** vault key (`SqliteEmailAccounts(conn, None)`
in `__main__.py`), so it cannot decrypt mailbox passwords. The main process can,
and already assembles every mailbox for the actor into the per-turn `servers`
dict (`ToolPolicy.turn`): owner `email_*` (env creds) and user `mail_*`
(decrypted). Both use `mcp-mail-server`.

- **New pure helper** `email_accounts_from_servers(servers) -> list[MailboxSpec]`
  (in `server_spec.py` or a sibling): scan the dict, select entries whose args
  reference `MAIL_PACKAGE` (`mcp-mail-server`), read their `env`
  (`IMAP_HOST/IMAP_PORT/IMAP_SECURE/EMAIL_USER/EMAIL_PASS`), and emit one
  `MailboxSpec` per mailbox keyed by the dict key (`cuenta`). One path for owner
  and users; ignores non-mail servers.
- **`ToolPolicy.turn`** serializes that list to JSON and sets it on the `ari`
  server spec env as `ARI_EMAIL_ACCOUNTS`, alongside the existing
  `ARI_ACTOR_ID` / `ARI_ROLE` / `ARI_TURN_ID`.
- **`__main__._get_tools`** parses `ARI_EMAIL_ACCOUNTS` into the
  `{cuenta: MailboxSpec}` lookup and constructs `ImapEmailReader`, passing both
  into `AriTools`.

**Security posture:** the `ari` server receives only the acting user's mailbox
credentials for that turn — the same credentials the turn config already writes
for the mail MCP — and **not** the vault key. This is strictly less privilege
than handing the server the key. The env lives only for the turn and is torn
down with the turn config.

## 7. Permissions

- Add `buscar_correos` and `leer_correo` to `ARI_TOOLS` and to `_USER_CHAT` in
  `src/ari/domain/tools/ari_permissions.py` so both owner and BYO users get them
  in CHAT.
- They remain **absent** from `TASK` and `HEARTBEAT` (those stay `_READ`-only).
- Both tools no-op gracefully with a clear message when the actor has no
  mailboxes resolved for the turn.

## 8. Edge cases & failure handling

- **Unknown `cuenta`** → "No tenés una casilla llamada «…»." listing available
  names.
- **No mailboxes at all** → "No tenés casillas de correo conectadas."
- **IMAP/TLS/auth failure or timeout** → short, non-leaky error ("No pude
  conectarme a la casilla …"); never surface the password.
- **Message UID not found** → "No encontré el correo #…".
- **HTML-only body that strips to nothing** → fall back to a note + rely on
  attachments.
- **Huge body over the text cap** → saved truncated with an explicit marker.
- **Attachment over cap / too many** → saved up to the limit, the rest reported.
- Coexistence: the mail MCP read tools still exist; descriptions steer the model
  to the native tools for reading, especially large emails.

## 9. Testing plan (strict TDD)

- `ImapEmailReader` against a mocked `imaplib` connection: `search` mapping and
  ordering; `fetch` of a multipart message (text + html + PDF attachment);
  charset/header decoding; html→text conversion; connection closed on error.
- `html_text` stripper: tags removed, entities decoded, whitespace collapsed.
- `UserWorkspace.write_bytes`: happy path, `max_bytes` rejection, jail/`..`
  rejection, parent creation, returned relative path.
- `AriTools.leer_correo`: reply format, body spill path, attachment spill paths,
  body-over-cap truncation, unknown `cuenta`, no mailboxes; uses a fake
  `EmailReaderPort`.
- `AriTools.buscar_correos`: criterio mapping, `limite` clamp, formatting, empty
  result.
- `email_accounts_from_servers`: owner `email_*` + user `mail_*` extracted,
  non-mail servers ignored, missing env handled.
- `ari_permissions`: `buscar_correos`/`leer_correo` present for owner and in
  `_USER_CHAT`; absent in `TASK`/`HEARTBEAT`.
- `ToolPolicy.turn`: `ARI_EMAIL_ACCOUNTS` present in the `ari` server env and
  matches the turn's mailboxes.

## 10. Files touched

New:

- `src/ari/domain/email/email_reader_port.py`
- `src/ari/infrastructure/email/imap_email_reader.py`
- `src/ari/infrastructure/email/html_text.py`

Changed:

- `src/ari/domain/email/entities.py` — add `MailboxSpec`, `EmailSummary`,
  `EmailAttachment`, `FetchedEmail`.
- `src/ari/infrastructure/email/server_spec.py` — `email_accounts_from_servers`.
- `src/ari/infrastructure/workspace/user_workspace.py` — `write_bytes`.
- `src/ari/application/ari_tools.py` — `buscar_correos`, `leer_correo`.
- `src/ari/application/tools/tool_policy.py` — inject `ARI_EMAIL_ACCOUNTS`.
- `src/ari/mcp_server/server.py` — two `@tool` wrappers.
- `src/ari/mcp_server/__main__.py` — build reader + account lookup from env.
- `src/ari/domain/tools/ari_permissions.py` — register the two tools.
- Tests under `tests/` mirroring the above.

## 11. Open questions

None blocking. The `cuenta` naming reuses the turn-visible server names
(`email_corp`, `mail_<label>`); if that proves confusing to users, a friendlier
alias layer can be added later without changing the architecture.
