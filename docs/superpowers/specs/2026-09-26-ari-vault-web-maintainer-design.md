# Ari — Vault web maintainer (Design)

- **Date:** 2026-09-26
- **Status:** Draft — pending user approval
- **Part of:** Secrets vault (vault + filesystem MCP → **this: web maintainer for loading secrets**)
- **Builds on:** the secrets vault (`SecretVault`/`FernetVault`, `ari.vault` CLI), the MCP
  registry (`McpRegistry`, `mcp/servers.json` `${VAR}` allowlist), the Telegram gateway
  (owner-gated `CommandHandler`s, `register_commands`), and the capability registry
  (`capabilities.py`). Reuses the `cryptography` dependency (Fernet + self-signed cert).

## 1. Purpose

Today the only way to put a secret in the vault is the terminal (`python3 -m ari.vault set`).
The owner wants to load MCP credentials from a phone without terminal access. After this
change:

- The owner sends an owner-only `/vault` command; Ari replies with a **single, key-protected
  HTTPS link** delivered only over Telegram.
- Opening the link on the same LAN shows a form listing the vault-backed secret names from
  `servers.json` (which are set, never their values); the owner types values and they are
  written encrypted to the vault.
- A freshly loaded secret takes effect **without restarting Ari** (the registry re-resolves).

Success: the owner, on their phone on the same WiFi, taps the Telegram link, loads
`GOOGLE_OAUTH_CLIENT_SECRET`, and within one turn `/conexiones` shows the Google server as
configured — with no plaintext secret ever crossing the wire in the clear, appearing in a
log, or reaching Ari's LLM.

## 2. Locked decisions

| Decision | Choice | Rationale |
|---|---|---|
| Reachability | **LAN** (bind `0.0.0.0`, link uses the machine's LAN IP) | User choice; load from phone on same WiFi |
| Transport | **HTTPS, self-signed cert** (via `cryptography`, no openssl) | Encrypts token + credential on the WiFi; browser warns once |
| Trigger | Owner-only `/vault` over Telegram; Ari replies with the link | Link delivered only to the owner; no standing public page |
| Server lifecycle | **On-demand**: starts on first link, shuts down when no live tokens | Minimizes the exposure window |
| Token | `secrets.token_urlsafe(32)` (256-bit), TTL 10 min, session (multi-submit within TTL) | Unguessable; short-lived; one visit can load several secrets |
| Writable names | **Allowlist** = `${VAR}` in `servers.json` minus `ARI_FS_ROOT` | No arbitrary keys; `ARI_FS_ROOT` is a path, not a vault secret |
| Live pickup | Registry hot-reload also watches the vault file mtime | A loaded secret takes effect without restart |
| Scope of writes | `set` only | Delete/rotate stay in the CLI (out of scope) |

## 3. Architecture (new package `infrastructure/vault_web/`)

```
infrastructure/vault_web/
  link_store.py   VaultLinkStore(ttl_seconds, clock): create()->token, valid(token)->bool,
                  sweep(), active_count  — in-memory, clock injected (pure/testable)
  cert.py         ensure_cert(dir, lan_ip)->(cert_path, key_path): self-signed via
                  cryptography, SAN=[lan_ip, 127.0.0.1], key chmod 0600, regen if IP changed
  names.py        configurable_secret_names(servers_json_path)->list[str]: ${VAR} names
                  minus ARI_FS_ROOT (the writable allowlist)
  server.py       VaultWebServer(vault, store, names): ThreadingHTTPServer + ssl in a daemon
                  thread; GET/POST /v/<token>; start()/stop(); redacted logging
  maintainer.py   VaultWebMaintainer(vault, servers_json, settings): owns store+cert+server;
                  new_link()->https URL (lazy-start, LAN-IP detect); reaper stops server when
                  active_count==0
```

- **Layering:** the web server is an infrastructure gateway (like `telegram_adapter`). The
  token store is in-memory infrastructure state. `FernetVault` (domain port + adapter) is the
  write target. No new domain port is needed — the maintainer composes existing pieces.
- **LAN IP detection:** open a UDP socket to a dummy address and read `getsockname()[0]`;
  fall back to `127.0.0.1` if detection fails (log a warning).

## 4. HTTP surface

Two routes, token in the path, on the SSL socket only (plain HTTP cannot connect):

- `GET /v/<token>` → if `store.valid(token)`: render an HTML form. It lists each allowlisted
  name with a badge "cargado" / "falta" derived from `vault.names()` (names only, **never
  values**), and one `<input type="password">` per name. Invalid/expired token → `403`.
- `POST /v/<token>` (`application/x-www-form-urlencoded`) → if valid: for each submitted
  `name` in the allowlist with a non-empty value, `vault.set(name, value)`; re-render with a
  success notice. A name outside the allowlist → `400`. Invalid/expired token → `403`.
- Any other path → `404`.

`BaseHTTPRequestHandler.log_message` is overridden to log only `method path-without-token
status` — never the token, query, or body.

## 5. Data flow

1. Owner sends `/vault` → owner-gated handler → `maintainer.new_link()` → creates a token,
   lazily starts the HTTPS server bound to the LAN, builds `https://<lan-ip>:<port>/v/<token>`.
2. Ari replies (Telegram) with the link + "vence en 10 min; el navegador va a advertir por el
   certificado, aceptá una vez".
3. Owner opens the link on the phone (same WiFi) → `GET` → form (allowlisted names, set/missing
   badges, empty password inputs).
4. Owner submits a value → `POST` → validate token + allowlist → `FernetVault.set()` →
   `~/.ari/vault.enc` rewritten atomically (`0600`) → success page.
5. The vault file mtime changed → the running `McpRegistry` re-resolves on its next refresh →
   the MCP server that needed that secret flips to configured. No restart.
6. Token TTL expires → `403`; when `active_count == 0`, the maintainer stops the server.

## 6. Security model

- **Transport:** HTTPS with a self-signed cert (SAN includes the LAN IP). The token and the
  typed credential are encrypted on the WiFi. The browser warns once (untrusted CA); the owner
  accepts.
- **Token:** 256-bit, delivered only via Telegram to the owner, 10-min TTL, session-scoped
  (multiple submits within the window; invalid after).
- **Exposure window:** the server runs only while a token is live and shuts down when idle.
- **Allowlist:** only `servers.json` secret names are writable; arbitrary keys are rejected.
- **No leakage:** values are never logged, echoed, or rendered back; the form shows only
  set/missing badges. Ari's LLM is not involved in this path.
- **At rest:** `FernetVault.set` writes atomically with `0600` (existing behavior).
- **Residual risk (stated plainly):** within the TTL, anyone on the LAN who obtains the link
  could use it. Mitigated by HTTPS (no sniffing), the short TTL, the unguessable token, and
  Telegram-only delivery. This is the accepted LAN posture (§2).

## 7. Concurrency

The web thread writes the vault while the main Ari process reads it (registry `_lookup`).
`FernetVault` is stateless per call and writes via temp-file + `os.replace`, so a reader always
sees either the old or the new file, never a partial one. No shared in-memory state, no lock
needed.

## 8. Configuration (additions)

| Variable | Default | Purpose |
|---|---|---|
| `ARI_VAULT_WEB_PORT` | `8765` | Port the HTTPS maintainer binds |
| `ARI_VAULT_WEB_TTL_MINUTES` | `10` | Link/token lifetime |
| `ARI_VAULT_WEB_BIND` | `0.0.0.0` | Bind address (LAN) |

Cert + key live in `~/.ari/` (`vault_web_cert.pem`, `vault_web_key.pem`, key `0600`).

## 9. Capability & command

Add to `CAPABILITIES` (`capabilities.py`): `Capability(command="vault",
menu="Cargar credenciales (link seguro)", owner_only=True, summary="Genera un link HTTPS de
un solo uso (vence pronto) para cargar credenciales MCP en la bóveda cifrada desde el
navegador, sin terminal.", usage="/vault → Ari te manda el link; ábrelo en la misma red y
carga los valores.")`. This registers the owner-menu entry and tells Ari about it, and drops
nothing from `limitations()`.

## 10. Registry live-pickup change

`McpRegistry.__init__` already receives the `vault`. Extend `_refresh` so its stamp tuple also
includes `_stamp(vault._path)` when a vault is present; a change to the vault file triggers the
same rebuild path as a `servers.json`/`.env` change. A missing vault file stamps as `None`
(like the existing `.env` handling) and does not error.

## 11. Testing

- `link_store`: create → valid; expiry via injected clock (valid before TTL, invalid after);
  `active_count` and `sweep`.
- `cert`: generates cert+key on first call, key mode `0600`, SAN contains the LAN IP; reuses on
  second call; regenerates when the IP argument changes.
- `names`: allowlist equals `servers.json` `${VAR}` minus `ARI_FS_ROOT`.
- `server` (integration): spin `VaultWebServer` on `127.0.0.1:0` with a temp cert and a
  `FakeVault`, hit it with `http.client` over an unverified TLS context —
  - `GET` valid token → 200, form contains the allowlisted names and set/missing badges, and
    contains **no** secret value;
  - `GET`/`POST` invalid or expired token → 403;
  - `POST` valid token, allowlisted name → `FakeVault.set` received the value;
  - `POST` name outside the allowlist → 400;
  - the handler's log output contains neither the token nor the value.
- `maintainer`: `new_link()` returns an `https://<ip>:<port>/v/<token>` URL and starts the
  server; the server stops when the last token expires (reaper).
- `registry`: a change to the vault file mtime triggers re-resolution (a secret added to the
  vault after construction is picked up on the next `servers_for`).

## 12. Out of scope

- Internet/tunnel exposure; multi-user access.
- Editing `.env` from the web (`ARI_FS_ROOT` stays a manual `.env` entry).
- Deleting or rotating secrets from the web (CLI only).
- A trusted (CA-signed) cert; the self-signed warning is accepted.

## 13. Acceptance criteria

1. Owner `/vault` → Ari replies with an `https://<lan-ip>:8765/v/<token>` link; a non-owner
   gets the "solo para el dueño" refusal.
2. Opening the link on the LAN shows the form; opening it after 10 min → 403.
3. Submitting `GOOGLE_OAUTH_CLIENT_SECRET` stores it encrypted; the form then shows it as
   "cargado" without ever displaying the value; the value appears in no log.
4. Within one turn after loading, `/conexiones` reflects the Google server as configured (no
   restart).
5. A `POST` with a name not in `servers.json` is rejected (400); the token cannot write
   arbitrary keys.
6. The credential and token are served only over HTTPS; a plain-HTTP client cannot connect.
