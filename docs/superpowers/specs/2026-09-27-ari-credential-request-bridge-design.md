# Ari — Credential request bridge (Design)

- **Date:** 2026-09-27
- **Status:** Draft — pending user approval
- **Part of:** Skill manager extensibility (Phase 2 of the skill-manager feature: natural-language + reactive credential intake, previously deferred).
- **Builds on:** the `vault_web` maintainer (`VaultWebMaintainer.new_link()`, HTTPS LAN form, allowlist already extended with skills' `required_secrets`), the skill manager (`SkillManager.list()` → `SkillStatus.state`/`missing_secrets`), the **`coding_requests` cross-process queue pattern** (`SqliteCodingRequests`, `CodingRequestRunner`, the `after_turn()` + `Scheduler` wiring), agent tools (`AriTools`, `ari_permissions.py`, `mcp_server/server.py`), and the shared SQLite `_SCHEMA` (`db.py`).

## 1. Purpose

Let the owner hand Ari a credential conversationally, and let Ari ask for one the moment it
actually needs it — without ever typing the secret into the Telegram chat.

- **Natural language:** the owner says "quiero pasarte el api de groq" (or similar). Ari
  understands, and replies with the `vault_web` HTTPS LAN link to load it.
- **Reactive on need:** when Ari tries to do something that needs a credential it does not
  have (e.g. a voice note arrives but `groq_audio` is `needs_secrets`), instead of a generic
  failure it sends that same link, naming what it needs.

The secret is still only ever entered in the LAN form and stored encrypted in the vault —
this feature only changes *how the link gets offered*, not how secrets are stored.

**The cross-process constraint that shapes this:** Ari's agent tools run in a per-turn,
DB-only MCP subprocess that cannot call the main process's `VaultWebMaintainer.new_link()`
(which starts the HTTPS server and holds tokens in memory). So the natural-language path
uses a **DB queue bridge** exactly like `coding_requests`: the subprocess tool enqueues a
request; a main-process runner mints the link and sends it. The reactive path already runs
in the main process, so it calls `new_link()` directly — no bridge.

Success: the owner types "pasame el link para el api de groq" → within a turn Ari replies
with `https://<lan-ip>:8765/v/<token>`; and separately, sending a voice note while
`GROQ_API_KEY` is missing yields that link instead of "no pude procesar ese audio".

## 2. Locked decisions

| Decision | Choice | Rationale |
|---|---|---|
| Natural-language path | **DB-queue bridge**, mirroring `coding_requests` | Agent tools run in a DB-only subprocess; the link must be minted in the main process |
| Reactive-on-need path | **Direct** `vault_web.new_link()` in the main-process handler | The handler already runs in the main process; no bridge needed |
| Proactivity level | **Reactive on real need only** (not a startup/tick scan) | User choice; avoids repeated unsolicited links |
| Owner-only | Tool gated owner + `CHAT` context (like `proponer_codigo`); reactive path gated `is_owner` | Credentials are owner business |
| Link scope | One `new_link()` link showing **all** missing writable names; message names the requested one | `new_link()` is not per-secret; the form's badges show what's missing |
| Request payload | A free-text `requested` hint (what the owner said), not a resolved secret name | No fuzzy name-mapping in the tool; the vault form is authoritative on real names |
| Runner shape | Simple: claim → `new_link()` → send → `finish(DONE)`; no background job | Delivering a link is instant, unlike `/code` planning |
| Storage | New `credential_requests` table in the shared `_SCHEMA` | Same idempotent-DDL path both processes run; no migration tooling |

## 3. Data model

Add to `_SCHEMA` in `src/ari/infrastructure/persistence/db.py` (created idempotently by both
`connect` and `open_existing`, exactly like `coding_requests`):

```sql
CREATE TABLE IF NOT EXISTS credential_requests (
  id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, chat_id TEXT NOT NULL,
  requested TEXT NOT NULL, status TEXT NOT NULL,
  created_at TEXT NOT NULL, detail TEXT);
CREATE INDEX IF NOT EXISTS idx_credential_requests_status
  ON credential_requests(status, id);
```

`src/ari/domain/credentials/requests.py` (mirrors `domain/coding/requests.py`):

```python
@dataclass(frozen=True)
class CredentialRequest:
    id: int
    user_id: str
    chat_id: str
    requested: str            # free-text hint of what the owner wants to provide
    created_at: datetime      # tz-aware UTC

PENDING, TAKEN, DONE, FAILED, SKIPPED = "pending", "taken", "done", "failed", "skipped"
```

## 4. Store — `SqliteCredentialRequests`

`src/ari/infrastructure/persistence/sqlite_credential_requests.py` — mirrors
`SqliteCodingRequests` (same `asyncio.Lock` + atomic `UPDATE … RETURNING`):

```python
class SqliteCredentialRequests:
    def __init__(self, conn: aiosqlite.Connection): ...
    async def add(self, user_id: str, chat_id: str, requested: str) -> int: ...
    async def claim_pending(self) -> list[CredentialRequest]:  # UPDATE status=taken WHERE status=pending RETURNING …
    async def reset_taken(self) -> list[CredentialRequest]:    # taken -> pending on restart
    async def finish(self, request_id: int, status: str, detail: str | None = None) -> None: ...
```

Atomic claim is the single `UPDATE credential_requests SET status=? WHERE status=? RETURNING …`
statement (WAL serialization), guarded by the lock — identical to the coding queue.

## 5. Agent tool — `pedir_credenciales`

`AriTools` gains a `credentials=None` kwarg (same pattern as `coding=`) and:

```python
async def pedir_credenciales(self, nombres: str) -> str:
    if not self._allowed("pedir_credenciales"):
        return DENIED
    text = (nombres or "").strip()
    if not text or len(text) > 500:
        return "No pude prepararlo: decime qué credencial querés cargar (1–500 caracteres)."
    if self._credentials is None:
        return "No pude prepararlo: la cola de credenciales no está disponible."
    await self._credentials.add(self._a.user_id, self._a.chat_id, text)
    return await self._receipt(f"🔑 Te preparo el link seguro para: {truncate(text)}")
```

Registered in `src/ari/mcp_server/server.py`:

```python
@tool("pedir_credenciales")
async def pedir_credenciales(nombres: str) -> str:
    """(Solo el creador) Cuando tu creador quiera darte una credencial/API/contraseña
    (p. ej. "quiero pasarte el api de groq"), prepara un link seguro para que la cargue
    sin escribirla en el chat. nombres: qué credencial quiere cargar."""
    return await (await get_tools()).pedir_credenciales(nombres)
```

Added to `ARI_TOOLS` in `src/ari/domain/tools/ari_permissions.py` — it lands only in
`set(ARI_TOOLS)`, so it is available to **owner + `CHAT` context only** (non-owners and all
scheduled/heartbeat contexts denied), exactly like `proponer_codigo`.

Wired in `src/ari/mcp_server/__main__.py` `_get_tools()`: pass
`credentials=SqliteCredentialRequests(conn)` alongside the existing `coding=` kwarg.

## 6. Runner — `CredentialRequestRunner`

`src/ari/application/credentials/request_runner.py`:

```python
class CredentialRequestRunner:
    def __init__(self, requests, vault_web, send, clock, max_age=timedelta(hours=1)):
        self._requests, self._vault_web = requests, vault_web
        self._send, self._clock, self._max_age = send, clock, max_age

    async def __call__(self) -> None:
        for req in await self._requests.claim_pending():
            if self._clock() - req.created_at > self._max_age:
                await self._requests.finish(req.id, SKIPPED, "stale")
                continue
            try:
                link = self._vault_web.new_link()   # sync; starts HTTPS server if needed
            except Exception as exc:
                await self._requests.finish(req.id, FAILED, str(exc))
                await self._send(req.chat_id, "No pude generar el link para cargar la credencial.")
                continue
            await self._send(req.chat_id,
                f"Para cargar {truncate(req.requested)} abrí este link en la misma red "
                f"(vence pronto):\n{link}\nEl form muestra todas las credenciales que "
                f"faltan; completá la que corresponda.")
            await self._requests.finish(req.id, DONE)
```

Wired in `main.py` `_post_init` exactly like `coding_runner`:
- construct `credential_requests = SqliteCredentialRequests(c.conn)` and
  `credential_runner = CredentialRequestRunner(credential_requests, c.vault_web, send, _utcnow)`;
- add `credential_runner` to the `Scheduler([...])` list and to `after_turn()`;
- at startup, `for r in await credential_requests.reset_taken(): await send(r.chat_id, "Retomo tu pedido de credencial…")` (or silently re-queue — see §8).

`new_link()` is synchronous and thread-safe (`threading.Lock`); calling it from the event
loop is safe because it is fast and offloads the server to a background thread.

## 7. Reactive-on-need path (main process, no bridge)

In `src/ari/main.py` `_on_message`, the voice branch currently sends a generic failure when
`voice_to_text` yields no text. Change it to check for a missing-secret cause **first**:

```python
if not text:
    missing = [s for s in app.bot_data["skills"].list()
               if s.state == "needs_secrets" and "inbound_transform" in s.hooks]
    if is_owner and missing:
        try:
            link = app.bot_data["vault_web"].new_link()
            needed = ", ".join(sorted({n for s in missing for n in s.missing_secrets}))
            await msg.reply_text(
                f"Necesito {needed} para procesar audio. Cargala en la misma red "
                f"(vence pronto):\n{link}")
        except Exception:
            await msg.reply_text("No pude procesar ese audio. ¿Lo escribís?")
    else:
        await msg.reply_text("No pude procesar ese audio (¿muy largo o error de transcripción?). ¿Lo escribís?")
    return
```

This is deterministic: an inbound-capable skill in `needs_secrets` is a concrete "needs a
credential" signal. If skills are active and transcription still failed, the generic message
stands.

## 8. Error handling

- `new_link()` raises (cert/bind/LAN detection failure) → runner `finish(FAILED)` + a plain
  "no pude generar el link" message; reactive path falls back to the generic audio message.
- Stale request (>1h, e.g. bot was down) → `finish(SKIPPED, "stale")`, no link (a link older
  than its TTL is useless).
- Restart with `taken` rows stranded → `reset_taken()` re-queues them to `pending` so the
  runner re-mints a fresh link; the owner gets a one-line "retomo tu pedido" notice.
- Runner never crashes the scheduler: `after_turn()` wraps it in try/except and logs, exactly
  like `coding_runner`.
- Non-owner (or a scheduled/heartbeat context) calling `pedir_credenciales` → `DENIED` by the
  permission table.
- Empty/oversized `nombres` → a clear message, nothing enqueued.

## 9. Architecture (new/changed)

```
domain/credentials/requests.py            CredentialRequest + status constants (new)
infrastructure/persistence/db.py          + credential_requests table in _SCHEMA
infrastructure/persistence/
  sqlite_credential_requests.py           SqliteCredentialRequests (new)
application/credentials/request_runner.py CredentialRequestRunner (new)
application/ari_tools.py                   + credentials kwarg + pedir_credenciales()
domain/tools/ari_permissions.py            + "pedir_credenciales" in ARI_TOOLS
mcp_server/server.py                       + @tool("pedir_credenciales")
mcp_server/__main__.py                     + credentials=SqliteCredentialRequests(conn)
main.py                                     construct store+runner; after_turn + Scheduler +
                                            reset_taken; reactive voice branch
```

No new dependency. Mirrors an existing, tested pattern end to end.

## 10. Testing (pytest + fakes; `python3 -m pytest`, `pythonpath=src`)

- `SqliteCredentialRequests` (mirror the coding-queue tests): `add` then `claim_pending`
  returns it as `taken`; a second `claim_pending` returns nothing (atomic); `reset_taken`
  moves `taken`→`pending`; `finish` sets terminal status.
- `pedir_credenciales` tool: owner+CHAT enqueues (a fake credentials store receives `add`);
  non-owner / non-CHAT → `DENIED`; empty/too-long `nombres` → message, no enqueue.
- `CredentialRequestRunner`: a pending request → calls a fake `vault_web.new_link()` (returns
  a canned URL) → `send` receives a message containing the URL **and** the requested hint →
  `finish(DONE)`; a stale request → `finish(SKIPPED)`, no send; `new_link()` raising →
  `finish(FAILED)` + fallback message.
- Reactive voice branch: with a fake skills manager whose `list()` reports `groq_audio`
  `needs_secrets` (inbound hook) and a fake `vault_web`, a voice update that transcribes to
  nothing → the link message naming the missing secret is sent (not the generic one); with all
  skills active → the generic failure message.
- `ari_permissions`: `pedir_credenciales` ∈ owner CHAT set, ∉ user/task/heartbeat sets.

## 11. Out of scope

- Proactive startup/tick scanning that volunteers links unprompted (deduping, cadence) — the
  chosen behavior is reactive-on-need only.
- Fuzzy mapping of "el api de audio" → the exact `GROQ_API_KEY` name (the vault form's badges
  already show which names are missing).
- Deleting/rotating secrets by conversation (CLI/vault_web only).
- Per-secret scoped links (`new_link()` shows all missing names by design).
- Reactive triggers for non-voice skills (only the voice inbound path exists today; the check
  generalizes when more inbound skills arrive).

## 12. Acceptance criteria

1. Owner says "quiero pasarte el api de groq" → Ari calls `pedir_credenciales`, and within the
   same turn (via `after_turn`) replies with a `vault_web` `https://…/v/<token>` link naming
   the requested credential.
2. A non-owner asking the same gets no tool and no link.
3. Sending a voice note while `groq_audio` is `needs_secrets` → Ari replies with the link and
   names `GROQ_API_KEY`, instead of "no pude procesar ese audio".
4. With `GROQ_API_KEY` loaded and `groq_audio` active, a voice note transcribes normally (no
   link).
5. A credential request left `taken` by a restart is re-queued and re-answered with a fresh
   link; a request older than the TTL is skipped, not answered with a dead link.
6. The runner failing to mint a link never crashes the bot; the owner is told plainly.
