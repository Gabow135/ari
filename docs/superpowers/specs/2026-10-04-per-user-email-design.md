# Per-user email: each user connects their own mailboxes (BYO)

- **Date**: 2026-10-04
- **Status**: Design — awaiting review
- **Author**: brainstorming session with the owner

## Intent

Today the three email MCP servers (`email_corp`, `email_gmail`, `email_hotmail`
in `mcp/servers.json`) are `access: owner` and resolve their credentials from a
single global `FernetVault`. They are the **owner's** mailboxes. Non-owner users
entering Ari do not get email tools at all.

The owner wants every user to connect **their own mailboxes** (bring-your-own):
Ari reads and sends from *that user's* account, never from the owner's, and no
user can ever touch another user's mail. Each user may connect **multiple**
mailboxes from day one.

This is exactly the gap the cross-user-grants design
(`2026-10-03-cross-user-grants-design.md`) deliberately left open: email was out
of scope there because it was "not per-user data — a shared MCP connection gated
by role". This change turns email into genuine per-user data.

### Decisions locked in during brainstorming

| Question | Decision |
|----------|----------|
| Who provides the account | **BYO** — each user connects their own mailbox, not shared access to the owner's. |
| Mailboxes per user | **Multiple** from day one. PK `(user_id, label)`. |
| Storage | **New SQLite table** `user_email_accounts`; password stored **encrypted** with the same Fernet key as the vault (`ARI_VAULT_KEY`). |
| Why not flat vault keys | The vault is a flat blob with a fixed name allowlist — it does not model N users × M accounts, nor the per-account metadata (host/port). Rejected. |
| Why not hybrid (metadata in DB, secret in vault) | Splits one account across two stores → two-phase writes/deletes for no real security gain (the key is already outside the DB either way). Rejected. |
| Owner's existing mailboxes | **Untouched.** `servers.json` `email_*` stay owner-only. The per-user table is additive. |
| Credential loading (the hard part) | **Sealed-box encrypted blob, zero ingress** (see below). Plaintext never passes through chat nor any open URL. |
| Loading channel vs LAN/remote | Users are a mix of same-LAN and remote. The blob mechanism works identically for both, so there is **one** loading flow, not two. |

## The loading problem and the chosen solution

Hard rules: a plaintext password must **never** pass through the chat (it would
be logged and stored in memory/recalls) **nor** through a publicly reachable
URL. The existing `vault_web` form is LAN-only, so it cannot reach remote users,
and the owner explicitly rejected any open public ingress.

**Chosen: sealed-box encrypted blob (zero ingress).**

1. Ari holds a long-lived **X25519 key pair**. The private key lives in the
   `FernetVault` under a reserved name (e.g. `ARI_SEALEDBOX_SK`), generated once
   on first use; if `ARI_VAULT_KEY` is unset the feature is disabled (same
   constraint as any other secret). The public key is embedded in a generated
   HTML file.
2. On `conectar_correo`, Ari sends the user a **self-contained HTML file as a
   Telegram document**. It opens offline (`file://`), contains Ari's public key,
   and presents a form: `label`, provider preset (Gmail / Outlook / Corporate /
   Custom → prefills IMAP/SMTP host, port, secure), `email_user`, `password`
   (app password).
3. On submit, **entirely client-side (no network)**, the HTML serializes the
   account to JSON and seals it to Ari's public key with libsodium
   `crypto_box_seal`, then shows a base64 string prefixed `ari-mail:v1:<b64>`
   with a copy button.
4. The user pastes that blob back into the chat. Ari decrypts it with its
   private key (`SealedBox`), validates the shape, stores the account for the
   **pasting user**, deletes the user's message, and confirms.

Because only Ari's private key can open a sealed box, the blob is useless to
anyone else even if it is logged or forwarded. The plaintext password never
leaves the user's device.

### Crypto library — decision point for review

- **Recommended**: libsodium sealed box — `crypto_box_seal` from an **inlined**
  `libsodium.js` in the generated HTML (offline-capable, no CDN), and PyNaCl
  `nacl.public.SealedBox` / `PrivateKey` on the Python side. Both call the
  identical audited primitive; the envelope is one function call on each end, so
  there is almost no room for a parametrization bug. New deps: `PyNaCl` (Python
  runtime), `libsodium.js` (inlined into the generated HTML only — not an app
  runtime dependency).
- **Alternative (zero new deps, more hand-rolled)**: WebCrypto ECDH P-256 +
  HKDF + AES-GCM in the browser, decrypted with the already-present
  `cryptography` library. Avoids new dependencies but hand-rolls the envelope
  (ephemeral key, HKDF salt/info, IV, tag), which is exactly where cross-stack
  crypto bugs appear.

Recommendation: take the sealed box. The dependency cost is small and the
correctness margin is large for a security-critical path.

## Architecture

### Data model (new table in `persistence/db.py` `_SCHEMA`)

Added with `CREATE TABLE IF NOT EXISTS`, so it migrates automatically on the
next `connect`/`open_existing`, like the `grants` table.

```sql
CREATE TABLE IF NOT EXISTS user_email_accounts (
  user_id     TEXT    NOT NULL,          -- Telegram user_id; the owner of this mailbox
  label       TEXT    NOT NULL,          -- user-chosen, slug-safe: 'trabajo', 'personal'
  imap_host   TEXT    NOT NULL,
  imap_port   INTEGER NOT NULL DEFAULT 993,
  imap_secure INTEGER NOT NULL DEFAULT 1,
  smtp_host   TEXT    NOT NULL,
  smtp_port   INTEGER NOT NULL DEFAULT 465,
  smtp_secure INTEGER NOT NULL DEFAULT 1,
  email_user  TEXT    NOT NULL,          -- address / login
  pass_enc    TEXT    NOT NULL,          -- Fernet(ARI_VAULT_KEY) ciphertext of the password
  created_at  TEXT    NOT NULL,
  PRIMARY KEY (user_id, label));
```

`pass_enc` is the only secret column; it is ciphertext, and the key stays
outside the DB in `ARI_VAULT_KEY`. Re-adding the same `(user_id, label)` upserts.

### New domain + infrastructure (hexagonal, matching the rest)

- `domain/email/entities.py` — `EmailAccount` dataclass (all fields above except
  `pass_enc`; carries the **plaintext** password in memory only transiently).
- `domain/email/email_accounts_port.py` — `EmailAccountsPort` protocol:
  `add(user_id, account)`, `list_for_user(user_id) -> list[EmailAccount]`,
  `remove(user_id, label) -> bool`, `delete_for_user(user_id) -> int`.
- `domain/crypto/secret_cipher.py` — small `SecretCipher` protocol
  (`encrypt(str) -> str`, `decrypt(str) -> str`). `FernetVault` only reads/writes
  disk blobs, so this is a separate primitive.
- `infrastructure/crypto/fernet_cipher.py` — `FernetCipher(key)` implementing
  `SecretCipher` with the same `ARI_VAULT_KEY`.
- `infrastructure/email/sqlite_email_accounts.py` — `SqliteEmailAccounts`
  implementing the port against `user_email_accounts` (same `asyncio.Lock` write
  pattern as the other stores), encrypting/decrypting `pass_enc` via
  `SecretCipher`.
- `infrastructure/email/sealed_box.py` — `AriSealedBox`: generate/load the
  X25519 key pair (private key in the vault), `public_key_b64()`, and
  `open(blob) -> dict`.
- `infrastructure/email/account_form.py` — pure `render_enroll_html(public_key_b64)`
  returning the self-contained HTML (inlined libsodium.js + public key + form +
  seal-on-submit JS). Provider presets live here.
- `infrastructure/email/server_spec.py` — pure `email_server_spec(account) -> dict`
  building the `mcp-mail-server@2.1.0` MCP entry (command/args + env). The npx
  package/version detail lives here only.

### Application use-case

- `application/email/connect_email_account.py` — `ConnectEmailAccount(user_id,
  blob) -> label`: open the sealed box, validate the JSON shape and the label
  (slug-safe, non-empty), build an `EmailAccount`, `EmailAccountsPort.add`.
  Rejects malformed blobs with a clear error, touching nothing.

### Runtime injection (no rewrite — the seam already exists)

`ToolPolicy.turn(user_id, …)` (`application/tools/tool_policy.py:77`) already
builds a **fresh per-turn MCP config per user** and deletes it in `finally`.
Inject the acting user's mailboxes there, after `servers =
self._registry.resolved(owner)` and before `self._writer.write(servers)`:

```python
for acct in await email_accounts.list_for_user(user_id):
    servers[f"mail_{acct.label}"] = email_server_spec(acct)
```

- The injected server name is prefixed `mail_` to **guarantee no collision**
  with the owner's `servers.json` `email_*` names.
- The decrypted password lives only inside that ephemeral `turn-*.json`, deleted
  when the call ends — identical to how owner secrets are handled today.
- `ToolPolicy.view(user_id)` also lists the user's mailboxes (label + masked
  address, e.g. `ju***@gmail.com`) so Ari knows it has them. The allowlist in
  `turn` already derives `mcp__<name>` from the final `servers` dict, so the new
  `mail_*` servers are permitted automatically.

`ToolPolicy` gains one injected dependency (an `email_accounts` reader). It must
stay ignorant of SQLite and npx — those are in the adapter and
`email_server_spec`.

### Loading flow wiring (gateway)

- New Ari MCP tool `conectar_correo()` → **enqueues an enroll request**, reusing
  the existing credential-request bridge pattern
  (`2026-09-27-ari-credential-request-bridge-design.md`): the MCP server
  (writer) adds a row to a queue; a runner in the bot process (claimer) picks it
  up. An `EmailEnrollRunner` — parallel to `CredentialRequestRunner` — claims the
  request, renders the HTML with `render_enroll_html(sealed_box.public_key_b64())`,
  and sends it to the user's chat as a **document** via `app.bot.send_document`
  (python-telegram-bot is already used directly for `send_voice`/`reply_text` in
  `main.py`). The tool does not send anything itself (it runs in the MCP server
  process, which has no Telegram client), exactly like how credential links are
  delivered today. The queue can be a dedicated `email_enroll_requests` table or
  a `requested` discriminator on the existing `credential_requests` queue —
  decided at task time; the bridge shape is the same either way.
- **Blob interception — critical.** `HandleMessage.__call__` persists the
  incoming message to memory on its first line
  (`handle_message.py:59`, `append_message`). A pasted blob must be intercepted
  **before** that, in `_dispatch` (`main.py`): if `text` matches
  `^ari-mail:v1:`, route it to `ConnectEmailAccount`, delete the user's Telegram
  message (`msg.delete()`), reply "Casilla «<label>» conectada ✅", and
  **return without calling the message handler** — the blob never reaches memory,
  the LLM, or the turn log. (It is ciphertext and safe even if stored; deleting
  and short-circuiting is defense in depth.)

### Management tools (MCP, natural language)

Callable by any user over **their own** mailboxes (actor-scoped, like the
existing access/grant tools):

- `conectar_correo()` — start the enroll flow (sends the HTML document).
- `mis_correos()` — list the actor's mailboxes: label + masked address + a
  reachable/degraded hint. **Never** the password.
- `olvidar_correo(label)` — `EmailAccountsPort.remove(actor, label)`.

Register these in `domain/tools/ari_permissions.py` (`ARI_TOOLS` +
`allowed_ari_tools` for the CHAT context, owner and non-owner). They are
sensitive: add them to the `_INJECTION_RULE` list so Ari only acts on them when
the user asks in their own message — never because some email or page said so.

### Isolation + lifecycle

- Every turn injects only the acting `user_id`'s mailboxes (keyed by `user_id`).
  No cross-user read is possible.
- On bot-access revocation (`on_revoke` in `main.py`, alongside
  `schedule.cancel_user` and `GrantPort.delete_for_user`), also call
  `EmailAccountsPort.delete_for_user(user_id)`.

## Data flow (example)

1. B: "Ari, conectá mi correo." → `conectar_correo()` → Ari sends `enroll.html`
   as a document.
2. B opens it offline, picks "Gmail", fills address + app password, copies the
   `ari-mail:v1:…` blob.
3. B pastes the blob. `_dispatch` intercepts it, `ConnectEmailAccount` decrypts
   and stores `(B, "gmail", …)`, deletes B's message, replies "Casilla «gmail»
   conectada ✅".
4. B: "Ari, mandale un mail a Juan." → that turn injects `mail_gmail` with B's
   credentials → Ari sends from B's mailbox.
5. B: "Ari, olvidá mi casilla gmail." → `olvidar_correo("gmail")`.

## Error handling

- Vault key unset → `conectar_correo` returns "No puedo guardar credenciales:
  falta ARI_VAULT_KEY" (no key pair, no storage).
- Malformed / unopenable blob → "No pude leer ese código; generá uno nuevo con
  el formulario." Nothing stored.
- Duplicate label → upsert (treated as "updated the mailbox «X»").
- Invalid label (empty or non-slug) → clear error, nothing stored.

## Testing strategy (strict TDD — observe RED first)

- `FernetCipher`: encrypt→decrypt round-trip; decrypt of garbage raises.
- `SqliteEmailAccounts`: add/list/remove/delete_for_user round-trips;
  `pass_enc` is ciphertext at rest (not the plaintext); **isolation** — user A
  never sees user B's rows; re-add upserts.
- `email_server_spec`: correct command/args/env; provider-preset values map to
  the right host/port/secure.
- `AriSealedBox`: generate→persist→reload the key pair; `open(seal(x)) == x`;
  tampered blob raises.
- `ConnectEmailAccount`: valid blob stores under the actor; malformed blob
  rejected; bad label rejected.
- `ToolPolicy.turn`: injects `mail_<label>` for the acting user only; name is
  prefixed (no collision with `email_*`); allowlist includes the injected
  servers. `ToolPolicy.view`: lists mailboxes with a masked address, never the
  password.
- Blob interception in dispatch: an `ari-mail:v1:` message is intercepted,
  stored, the user message deleted, and **never** passed to the handler /
  appended to memory.
- Management tools: `mis_correos` masks the address; `olvidar_correo` removes;
  `_INJECTION_RULE` includes the new tools.
- Access revocation deletes the user's mailboxes.

## Out of scope (YAGNI)

- OAuth mailboxes (Gmail/Outlook OAuth). v1 is IMAP/SMTP + app password, matching
  the owner's existing `mcp-mail-server` servers.
- Per-user quotas on mailbox count (rely on the user adding what they need).
- Migrating the owner's `servers.json` mailboxes into the table — they keep
  working as-is.
- A key-rotation flow for `ARI_SEALEDBOX_SK` (single long-lived pair in v1).
- Reusing/adapting the LAN `vault_web` form — the blob flow serves everyone, so
  the LAN form is left untouched for owner secrets only.

## Open limitations noted

- The enroll HTML requires the user to open a local file and paste back a blob —
  more friction than a hosted form, accepted as the price of zero ingress.
- A single long-lived Ari key pair: rotating it invalidates any enroll HTML a
  user has saved but not yet submitted (they regenerate — cheap).
- `mcp-mail-server@2.1.0` is pinned; per-user mailboxes inherit whatever that
  package supports (IMAP/SMTP).
