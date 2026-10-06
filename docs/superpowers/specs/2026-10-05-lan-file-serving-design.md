# Ari LAN file-serving (`abrir_archivo`) — Design

- **Date:** 2026-10-05
- **Status:** Approved (conversational design) — pending written-spec review
- **Topic:** An owner-only tool that mints a one-time LAN HTTPS link to open any file on the owner's Mac (except a hard secrets denylist), reusing the `vault_web` subsystem.

## 1. Context & problem

Ari can create/read files in the per-user `.ari/` workspace and extract text from
documents, but the owner also has files **elsewhere on the Mac** (invoices in
`~/Desktop`, `/tmp/…`, etc.) and wants to **open them directly in a browser** —
Telegram cannot open a local file. Ari already ships a LAN HTTPS mechanism for
exactly this shape: `/vault` mints a short-lived, TLS, token-gated link on the
local network (`src/ari/infrastructure/vault_web/`). This feature reuses that
mechanism to serve a file over the LAN.

## 2. Locked decisions (from brainstorming)

- **Scope:** any absolute path on the Mac, **except a hard secrets denylist**
  (the guardrailed option the user chose over "no restrictions").
- **Owner-only.** Only the owner can mint a file link. Non-owners cannot.
- **One-time token + short TTL + TLS**, stronger than `/vault`'s TTL-multi-use
  token: the file token is invalidated on the first successful GET.
- **Reuse `vault_web`** (server, cert, LAN-IP detection, on-demand start /
  auto-stop), rather than a separate server/port.
- **Conversational:** Ari mints the link when asked in natural language ("pasame
  / abrí el archivo X"), not only via a slash command.

## 3. Goals / non-goals

**Goals**
- `abrir_archivo(ruta)` (owner-only) → Ari replies with a LAN link that opens
  that file in the browser.
- Never serve a secret: a hard denylist enforced at every stage.
- Reuse the existing LAN HTTPS server, cert, and lifecycle.

**Non-goals (out of scope)**
- Serving directories or directory listings.
- Non-owner access.
- Writing/uploading files over the LAN (read/serve only).
- Remote (non-LAN) access or public URLs.
- Removing or relaxing the existing `sensitive` jail.

## 4. Architecture

The ari MCP server runs as a **subprocess** per turn; it cannot call the
main-process `VaultWebMaintainer` directly. So this mirrors the existing
**credentials request-queue pattern** (`pedir_credenciales` →
`SqliteCredentialRequests` → `CredentialRequestRunner` → `vault_web.new_link`):

### 4.1 Data flow
1. **`AriTools.abrir_archivo(ruta)`** (MCP subprocess, owner-only): expand `~`,
   `realpath`, verify the file exists, is a **regular file**, and is **not
   denied** (`is_denied`). On any failure, return a friendly Spanish message
   (not found / is a directory / "no comparto ese archivo" for denied). On
   success, enqueue a request via `SqliteFileRequests.add(user_id, chat_id,
   path)` and return a receipt ("🔗 Te preparo el link para abrir «…»").
2. **`SqliteFileRequests`** (new `file_requests` table): queue between the MCP
   server (writer, `add`) and the bot (claimer, `claim_pending`), mirroring
   `SqliteCredentialRequests` (`add` / `claim_pending` / `reset_taken` /
   `finish` / `status_of`).
3. **`FileServeRequestRunner`** (new, main process, polled on the existing
   runner cadence): `claim_pending()` → for each request, drop if stale
   (> max_age), **re-validate** path (`is_denied` again — defense in depth),
   mint via `vault_web.new_file_link(path)`, `send(chat_id, link)`, `finish`.
   On startup, `reset_taken()` re-queues stranded rows (a file link is cheap to
   re-mint, like credentials).
4. **`VaultWebMaintainer.new_file_link(path)`** (new method): validate
   (`realpath` + `is_denied` + regular file), `FileLinkStore.create(path)` →
   token, start the server on demand (same as `new_link`), return
   `https://<lan_ip>:<port>/f/<token>`.
5. **`VaultWebServer` `/f/<token>` GET** (new route in `_Handler.do_GET`):
   `path = file_store.claim(token)` (single-use pop + TTL); if `None` → 403
   "Link vencido o inválido."; **re-check** `is_denied(path)` and regular-file
   (defense in depth at serve time); stream the file in chunks with
   `Content-Type` from `mimetypes` and `Content-Disposition: inline`
   (`filename=…`); **audit-log** the served absolute path (token redacted, as
   the handler already does).

### 4.2 New components
- `src/ari/infrastructure/vault_web/file_link_store.py` — `FileLinkStore`
  mapping token→path, **single-use** (`claim` pops), TTL + injected clock;
  `create(path) -> token`, `claim(token) -> str | None`, `active_count()`,
  `sweep()`. Mirrors `VaultLinkStore`.
- `src/ari/infrastructure/vault_web/fs_denylist.py` — `is_denied(path,
  denied_roots) -> bool` and a builder `default_denied_roots(settings)`.
  `is_denied` resolves `realpath` then returns True if the real path equals or
  is under any denied root, **or** its basename is `.env`.
- `src/ari/infrastructure/persistence/sqlite_file_requests.py` —
  `SqliteFileRequests` (+ a `file_requests` table in the schema).
- `src/ari/domain/files/requests.py` — `FileRequest` dataclass + `PENDING`/
  `TAKEN`/`DONE`/`FAILED`/`SKIPPED` statuses (mirror credentials).
- `src/ari/application/files/request_runner.py` — `FileServeRequestRunner`.
- `AriTools.abrir_archivo` + `ARI_TOOLS`/`_USER_CHAT` gating (owner-only) + a
  `@tool("abrir_archivo")` wrapper + a `capabilities.py` entry (owner_only).

### 4.3 Changes to existing components
- `VaultWebMaintainer`: own a `FileLinkStore`; add `new_file_link`; pass the
  file store to `VaultWebServer`; `sweep_and_maybe_stop` stops the server only
  when **both** the vault store and the file store have 0 active tokens.
- `VaultWebServer` / `_Handler`: accept a `file_store` + `denied_roots`; add the
  `/f/<token>` GET route. `/v/<token>` is unchanged.
- `main.py build()`: construct `SqliteFileRequests`, `default_denied_roots`,
  wire `file_store`/`denied_roots` into the maintainer, construct
  `FileServeRequestRunner`, poll it (alongside the credential runner), and pass
  `file_requests` to `AriTools`. `_get_tools` in the MCP `__main__` also gets
  `SqliteFileRequests`.

## 5. Security model (the crux)

- **Owner-only minting.** `abrir_archivo` is gated owner-only in
  `allowed_ari_tools` (not in `_USER_CHAT`) and by `self._allowed`.
- **Hard denylist, checked at THREE stages** (tool, runner mint, server serve),
  each with `realpath` first so a symlink cannot smuggle a denied target past
  the check. Denied roots (built from settings + `~`):
  `settings.vault_path`, the vault-web cert key, `~/.ssh`, `~/.aws`,
  `~/.gnupg`, `~/Library/Keychains`, `os.path.abspath(settings.claude_config_dir)`;
  plus any file whose basename is `.env`. The workspaces dir and ordinary user
  folders (Desktop, Downloads, /tmp) remain servable.
- **One-time token:** `FileLinkStore.claim` removes the token, so a leaked link
  works at most once; plus the short TTL.
- **TLS** via the existing self-signed cert; link is `https://<lan_ip>:<port>/f/<token>`.
- **Regular files only:** directories, devices, sockets, and symlinks-to-denied
  are rejected; no directory listing, never serves `/`.
- **Audit log:** each serve logs the absolute path (token redacted).
- **No new exposure of the `sensitive` jail:** the denylist is a superset of the
  secret members of `sensitive`; the project root itself is servable only as
  ordinary files (its `.env` is denied), which is acceptable for owner-only use.

## 6. Testing strategy

Pytest + pytest-asyncio, following existing `vault_web` and request-runner tests.

- `FileLinkStore`: create→claim returns the path once, second claim → None
  (single-use); expired token → None; unknown token → None.
- `is_denied`: denies vault path, `.env` (by basename), `~/.ssh/...`,
  keychains; allows a file in Desktop/Downloads/workspace; a symlink pointing
  at a denied target is denied (realpath).
- `VaultWebServer` `/f/<token>`: valid token streams bytes with the right
  Content-Type; invalid/expired → 403; a denied path (if it somehow reaches the
  store) → refused at serve time; `/v/<token>` still works.
- `VaultWebMaintainer.new_file_link`: starts the server, returns a `/f/` URL;
  `sweep_and_maybe_stop` keeps the server up while a file token is active and
  stops it when both stores drain.
- `SqliteFileRequests`: add → claim_pending → finish; reset_taken re-queues.
- `FileServeRequestRunner`: pending → link sent; stale → skipped; mint failure
  → reported; denied path → failed/refused without a link.
- `AriTools.abrir_archivo`: owner in chat enqueues + receipt; non-owner →
  DENIED; a denied path → friendly refusal, nothing enqueued; a missing file →
  friendly "no existe".
- Permission/catalogue: `abrir_archivo` in `ARI_TOOLS`, owner-only; update the
  `test_ari_permissions.py` catalogue pins and `test_mcp_server` parity.

## 7. Risks & open questions

- **Browser double-fetch vs single-use:** a client that issues a HEAD or a retry
  could consume the token before the real GET. Mitigation: only `GET` consumes
  the token; `HEAD` is not offered. Documented tradeoff; TTL covers the rest.
- **0.0.0.0 bind:** the vault server binds `settings.vault_web_bind` (default
  `0.0.0.0`). One-time + TTL + TLS + denylist bound the exposure; a future
  hardening could bind the detected LAN IP only. Not changed here.
- **Denylist completeness:** first-pass list; easy to extend. It is a denylist,
  not an allowlist (the user chose broad access) — new secret locations must be
  added as discovered.
- **Large files:** streamed in chunks; no explicit size cap (owner-only, own
  machine). A cap can be added if needed.

## 8. Out of scope / future

- Allowlist mode (serve only specified roots) — a stricter alternative if the
  threat model tightens.
- Upload/write over the LAN.
- Binding to the LAN IP instead of `0.0.0.0`.
