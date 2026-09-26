# Ari — Skill manager & Groq audio skill (Design)

- **Date:** 2026-09-26
- **Status:** Draft — pending user approval
- **Part of:** Extensibility (this: **skill manager + first-party Groq audio skill + LAN credential intake**) → Phase 2 (dynamic skill creation via the coding agent).
- **Builds on:** Secrets vault (`SecretVault`, `FernetVault`, resolution order vault → env → `.env`), the **`vault_web` maintainer** (`VaultWebMaintainer`, `/vault`, HTTPS LAN credential form — reused for credential intake), `McpRegistry` hot-reload-by-mtime pattern, `Authorizer.is_owner`, Telegram gateway (`TelegramAdapter`, `_on_message`, `_dispatch`), `HandleMessage`, agent tools (`ari_tools.py`).

## 1. Purpose

Give Ari a **skill manager**: a subsystem where owner-only **code plugins** add new
abilities through small, typed hook contracts, with each plugin's credentials stored
**encrypted in the vault** — never in the Telegram chat and never in plaintext on disk.

The first concrete skill is **Groq audio** (bidirectional voice): a Telegram voice note
is transcribed to text before Ari's turn, and Ari's reply can be spoken back as a voice
message.

The owner drives everything **from Telegram** in natural language ("activá el skill de
audio"). When a skill needs a credential, Ari does **not** ask for it in the chat. It
**reuses the existing `vault_web` maintainer** to hand the owner a short-lived HTTPS LAN
link; the owner enters the API key in that form, the value is stored encrypted in the
vault, and the skill activates on the next reload.

Success: the owner asks Ari (by Telegram) to enable the Groq audio skill; Ari replies with
the existing `vault_web` `https://<lan-ip>:<port>/v/<token>` link; the owner opens it on the
same network, pastes `GROQ_API_KEY`; from then on voice notes are transcribed and Ari can
reply with voice — with the key encrypted in `vault.enc`, absent from the chat history, and
absent from logs.

## 2. Locked decisions

| Decision | Choice | Rationale |
|---|---|---|
| What is a skill | **Code plugin**: a local dir with `skill.json` + `skill.py` | User choice; matches the coding-agent generation path (Phase 2) |
| Origin | **Local only**, owner-authored / Ari-authored; **no** git/registry install | User choice; smallest trust surface for a personal bot |
| Execution | **In-process**, first-party skills only in v1 (no sandbox yet) | Only trusted (owner/Ari-written) code runs; sandbox is justified in Phase 2 |
| Management surface | **Owner-only Telegram slash commands** in the main process (`/skills`, `/skill_on`, `/skill_off`) | Agent tools run in a DB-only subprocess and can't mint the LAN link; slash commands run in the main process. Natural-language control deferred (§16) |
| Credential intake | **Reuse `vault_web`** (`VaultWebMaintainer`, `/vault`): HTTPS self-signed, one-time token, TTL, idle reaper | Already built, tested, wired; more secure (HTTPS) than a new server. Only the writable allowlist is extended to include skill secrets |
| Credential storage | `FernetVault.set()` (same vault as MCP secrets) | User requirement ("use la bodega para guardar las credenciales") |
| Hook contracts | Typed ports: `InboundTransform`, `OutboundTransform` (v1) | Approach A; explicit, testable boundaries |
| Secret access | `ctx.secret(name)` scoped to the manifest's `required_secrets` | Least-privilege via the manifest |
| Hot-reload | By file mtime, like `McpRegistry` | Consistency; enable/disable without a restart |
| Audio scope | **Bidirectional**: STT inbound + TTS outbound | User choice |
| Groq STT | `whisper-large-v3-turbo`, `POST /openai/v1/audio/transcriptions` | Fast/cheap; accepts `ogg` (Telegram voice) with no transcode |
| Groq TTS | `canopylabs/orpheus-v1-english`, `POST /openai/v1/audio/speech` | Verified available; returns WAV |
| Reply delivery | **Text + voice** when the turn came from voice | Text is the fallback if TTS fails; accessibility |

## 3. Skill contracts (domain, pure)

`domain/skills/models.py` — data carried across the boundary:

```python
@dataclass(frozen=True)
class Attachment:
    kind: str            # "voice" | "audio"
    mime: str            # e.g. "audio/ogg"
    data: bytes          # raw bytes (skill never touches the Telegram SDK)
    filename: str
    duration: int | None = None

@dataclass(frozen=True)
class RawInbound:
    user_id: int
    chat_id: int
    text: str | None
    attachment: Attachment | None = None

@dataclass(frozen=True)
class Delivery:
    kind: str            # "voice" | "audio" | "text"
    data: bytes | None = None
    mime: str | None = None
    text: str | None = None

@dataclass(frozen=True)
class InboundContext:      # what the outbound side needs to know about the origin
    came_from_voice: bool
    chat_id: int
    user_id: int
```

`domain/skills/contracts.py` — the hook ports (Protocols):

```python
class SkillContext(Protocol):
    def secret(self, name: str) -> str: ...   # scoped to manifest required_secrets; raises if not declared
    @property
    def http(self) -> HttpClient: ...
    @property
    def config(self) -> dict: ...             # skill.json "config" block
    @property
    def log(self) -> Logger: ...

class InboundTransform(Protocol):
    async def on_inbound(self, raw: RawInbound, ctx: SkillContext) -> str | None: ...
    # returns transcribed/derived text, or None to pass through to the next skill

class OutboundTransform(Protocol):
    async def on_outbound(
        self, reply: OutgoingMessage, origin: InboundContext, ctx: SkillContext
    ) -> list[Delivery] | None: ...
    # returns extra deliveries (e.g. a voice rendering), or None
```

A skill object may implement one or both hook protocols. The manifest's `hooks` list
declares which, so the manager can wire it without importing everything.

## 4. Manifest schema

`skills/<name>/skill.json` (same JSON house style as `mcp/servers.json`):

```json
{
  "name": "groq_audio",
  "version": "0.1.0",
  "enabled": true,
  "owner_only": true,
  "description": "Transcribe incoming voice and reply with voice via Groq.",
  "entrypoint": "skill.py",
  "factory": "build_skill",
  "required_secrets": ["GROQ_API_KEY"],
  "hooks": ["inbound_transform", "outbound_transform"],
  "config": {
    "stt_model": "whisper-large-v3-turbo",
    "tts_model": "canopylabs/orpheus-v1-english",
    "tts_voice": "troy",
    "reply_with_voice": true
  }
}
```

`entrypoint` is a module file inside the skill dir; `factory` is a callable
`build_skill(config: dict) -> object` returning the skill instance. `config` is
skill-private and validated by the skill itself, not by the manager.

## 5. SkillManager (application)

`application/skills/skill_manager.py`

Responsibilities:
- **Discover** subdirectories of `ARI_SKILLS_DIR` that contain a valid `skill.json`.
- **Validate secrets:** for each `enabled` manifest, check every `required_secrets` name
  resolves (vault → env → `.env`). If any is missing, the skill is **not activated**; it
  is recorded as `needs_secrets` with the missing names.
- **Load** activated skills via `SkillLoader` and build a `SkillContext` per skill whose
  `secret()` is restricted to that manifest's `required_secrets`.
- **Expose** the wired hooks: `inbound_transforms(is_owner)` and
  `outbound_transforms(is_owner)`, filtered by `enabled` + `owner_only`.
- **Hot-reload** by mtime: on each accessor call, if any `skill.json` mtime changed,
  rebuild (mirrors `McpRegistry._refresh`).
- **Status** for the management tools: `list()` → name, version, enabled, state
  (`active` | `needs_secrets` | `failed`), missing secrets, hooks.

A skill whose module raises on load is isolated and marked `failed`; the rest keep
working. The bot never crashes because one skill is broken.

## 6. SkillLoader (infrastructure)

`infrastructure/skills/skill_loader.py` — the only place that touches `importlib`:
loads `skills/<name>/<entrypoint>` as a module, calls `factory(config)`, returns the
instance. Kept separate so `SkillManager` is tested with a fake loader (no real imports).

## 7. Data flow

### 7.1 Inbound voice → text

1. Telegram update lands in `_on_message` (`main.py`).
2. `TelegramAdapter` gains a branch: if `msg.voice`/`msg.audio` is present, download the
   bytes (PTB `File.download_as_bytearray()`) and build
   `RawInbound(user_id, chat_id, text=None, attachment=Attachment("voice", "audio/ogg", data, ...))`.
   Plain text still produces `RawInbound(text=...)` unchanged.
3. The gateway asks `SkillManager.inbound_transforms(is_owner)` and runs them in order,
   stopping at the first that returns a non-`None` string.
4. `groq_audio.on_inbound` posts the bytes to Groq Whisper and returns the transcript.
5. The resulting text becomes the usual `IncomingMessage(user_id, chat_id, text)` and
   flows into the existing `_dispatch` → `HandleMessage` pipeline. The rest of Ari is
   unaware the text came from audio. The `InboundContext(came_from_voice=True, ...)` is
   remembered for the outbound side (kept in `bot_data`/per-turn context).
6. If the message has no text and no inbound skill handled it → Ari replies "no pude
   procesar ese audio" (today such messages are silently dropped).

### 7.2 Outbound text → voice

1. `HandleMessage` returns `OutgoingMessage(text)` as always.
2. Before sending, the sender asks `SkillManager.outbound_transforms(is_owner)`, passing
   the reply and the `InboundContext`.
3. `groq_audio.on_outbound`: if `came_from_voice` and `reply_with_voice`, call Groq TTS
   and return `[Delivery("voice", data=wav_bytes, mime="audio/wav")]`; else `None`.
4. The sender translates each `Delivery` to the right Telegram call (`send_voice`;
   fallback `send_audio`). The original **text is still sent** so a TTS failure never
   costs the answer.

## 8. Credential intake — reuse `vault_web`

Ari already ships a LAN credential-intake web form: `vault_web` (`VaultWebMaintainer`,
`/vault` command; see `2026-09-26-ari-vault-web-maintainer-design.md`). It already provides
exactly what a skill needs, and does it more securely than a from-scratch server:

- HTTPS self-signed (SAN = LAN IP), LAN-IP autodetect, on-demand start + idle reaper
  (`sweep_and_maybe_stop`, run by the scheduler).
- 256-bit token (`secrets.token_urlsafe(32)`), 10-min TTL, session (multi-submit).
- A form listing the writable secret **names** (never values) with set/missing badges;
  `POST` writes `FernetVault.set()`; the registry picks up the new secret on the next
  vault-mtime refresh (no restart).

This change therefore builds **no** new server. The only gap: the writable allowlist is
`configurable_secret_names(servers_json)` = `${VAR}` in `mcp/servers.json` minus
`ARI_FS_ROOT`, which does **not** include a skill's `GROQ_API_KEY` (declared in
`skill.json`, not `servers.json`). Two small edits close it:

1. `SkillManager.required_secret_names() -> list[str]` — the deduplicated union of
   `required_secrets` across discovered skills.
2. `VaultWebMaintainer` gains an optional `extra_names: Callable[[], list[str]] | None`.
   In `new_link()` the writable names become
   `dedup(configurable_secret_names(servers_json) + extra_names())`. `None` keeps today's
   behavior exactly. The composition root passes `skill_manager.required_secret_names`.

The `VaultWebServer` allowlist check (name outside the allowlist → `400`) is unchanged; a
skill secret is now simply a member of that allowlist.

**Skill-enable flow:** when the owner enables a skill whose secrets are missing, the
management tool calls `maintainer.new_link()` and returns the existing HTTPS
`https://<lan-ip>:<port>/v/<token>` link. The form now shows the skill's secret name with a
"falta" badge; loading a value stores it encrypted and the skill activates on the next
`SkillManager` reload. Nothing about the credential ever touches the Telegram chat or logs.

## 9. Groq audio skill (first-party)

`skills/groq_audio/` — `skill.json`, `skill.py` (`build_skill`), `groq_client.py`.

- `groq_client.py` wraps two calls against `https://api.groq.com`, auth
  `Authorization: Bearer <GROQ_API_KEY>` (read via `ctx.secret`):
  - **STT:** multipart `POST /openai/v1/audio/transcriptions` with `file` (the ogg bytes),
    `model`, optional `language`; parse `text`.
  - **TTS:** `POST /openai/v1/audio/speech` with `model`, `input`, `voice`,
    `response_format="wav"`; return the audio bytes.
- `skill.py` implements `on_inbound` (STT) and `on_outbound` (TTS), reading models/voice
  from `config`. It receives/returns **bytes** only — no Telegram, no disk.

## 10. Management from Telegram (owner-only slash commands)

Ari's agent tools run in a **separate per-turn MCP subprocess** that only reaches the DB
(`mcp_server/__main__.py`), so they cannot call the main process's `SkillManager` or
`VaultWebMaintainer` — and the LAN link must be minted in the main process (it starts the
HTTPS server and holds the token in memory). Therefore v1 manages skills with **owner-only
Telegram slash commands registered in the main process**, exactly like `/vault`:

- `/skills` → list each skill: name, version, enabled, state (`active` | `needs_secrets` |
  `failed`), missing secrets.
- `/skill_on <name>` → set `enabled=true` and reload. If the skill is `needs_secrets`, reply
  with `VaultWebMaintainer.new_link()` (the HTTPS `vault_web` link).
- `/skill_off <name>` → set `enabled=false` and reload.

Each handler is owner-gated with `app.bot_data["gate"].is_owner(...)` (the `/vault` pattern);
non-owners get the "solo para el dueño" refusal. Each command is also a `Capability(...,
owner_only=True)` in `capabilities.py`, which feeds the Telegram menu and Ari's prompt.

Natural-language management ("activá el skill de audio") is **out of scope for v1** because
it needs a cross-process bridge (a subprocess tool queuing an intent the main process acts
on); see §16.

## 11. Configuration (additions)

| Variable | Default | Purpose |
|---|---|---|
| `ARI_SKILLS_DIR` | `./skills` | Where local skill plugins live |

Credential intake reuses `vault_web`'s existing config (`ARI_VAULT_WEB_PORT`,
`ARI_VAULT_WEB_TTL_MINUTES`, `ARI_VAULT_WEB_BIND`) — **no new intake variables**.
`GROQ_API_KEY` is **not** a `Settings` field — it lives in the vault and is read only by
the skill via `ctx.secret`. `Settings` gains `skills_dir` (`ARI_`-prefixed).

## 12. Architecture (new/changed)

```
domain/skills/
  models.py              Attachment, RawInbound, Delivery, InboundContext
  contracts.py           SkillContext, InboundTransform, OutboundTransform (Protocols)
application/skills/
  skill_manager.py       discover, validate secrets (vault→env→.env), load, expose hooks,
                         hot-reload, required_secret_names()
infrastructure/skills/
  skill_loader.py        importlib loader (isolated, mockable)
skills/groq_audio/
  skill.json             manifest (required_secrets: GROQ_API_KEY)
  skill.py               build_skill: on_inbound (STT) + on_outbound (TTS)
  groq_client.py         Groq STT/TTS HTTP client
infrastructure/vault_web/maintainer.py  EXTEND: optional extra_names provider unioned into
                         the writable allowlist in new_link()
domain/agent/capabilities.py  + Capability entries: skills, skill_on, skill_off (owner_only)
infrastructure/gateway/telegram_adapter.py  voice branch → RawInbound; Delivery → send_voice
config/settings.py       + skills_dir
main.py                  construct SkillManager; bot_data["skills"]; voice I/O in _dispatch;
                         /skills /skill_on /skill_off owner-only CommandHandlers; pass
                         skill_manager.required_secret_names as VaultWebMaintainer extra_names
```

No new dependency: credential intake reuses `vault_web` (stdlib `http.server` + TLS via the
existing `cryptography` dep). The Groq client uses the existing HTTP stack (`httpx`, already
present via PTB — verified `httpx 0.28.1`, PTB 22.8).

## 13. Security boundary (honest)

- **Secrets never enter the Telegram chat or logs.** They are entered in a LAN form and
  stored with `FernetVault.set()`.
- **Credential intake** inherits `vault_web`'s posture: HTTPS self-signed, 256-bit token,
  10-min TTL, on-demand + idle-reaped server, redacted logging, allowlisted names only. The
  documented residual risk (anyone on the LAN who obtains the live link within the TTL) is
  the accepted LAN posture from the vault-web design; this change does not weaken it.
- **In-process execution** is safe in v1 only because skills are first-party
  (owner/Ari-written). Running *generated* skills from a Telegram request is explicitly
  **out of scope** here (Phase 2) precisely because it needs a sandbox + contract
  validation + review.
- **Least-privilege:** a skill can read only the secrets it declared; requesting another
  raises.
- **Owner-only:** discovery, management tools, and `owner_only` skills are all behind
  `Authorizer.is_owner`.

## 14. Error handling

- Groq STT fails/timeout → reply in text ("no pude transcribir, ¿lo escribís?"), log the
  error without the audio.
- Audio over the size limit (25 MB free / 100 MB dev) → early reject, never sent to Groq.
- Groq TTS fails → automatic fallback to text only.
- `send_voice` rejects the WAV → fall back to `send_audio` (OGG/Opus transcode via ffmpeg
  is a noted future improvement, not v1).
- Missing secret → skill stays `needs_secrets`; Ari returns the LAN link instead of
  crashing startup.
- Skill raises on load → isolated, marked `failed`, rest of Ari unaffected.
- Intake token unknown/expired → `vault_web` returns `403` (unchanged); a name not in the
  (now skill-extended) allowlist → `400` (unchanged).

## 15. Testing (pytest + fakes; `python3 -m pytest`, `pythonpath=src`)

Fast (no network, no Telegram, no real HTTP server):

- `test_skill_manager.py`: discovery of valid/invalid manifests; secret validation against
  a `FakeVault` (active vs `needs_secrets`); hot-reload on mtime change; a load error →
  `failed` and others still active; hook filtering by `enabled`/`owner_only`.
- Contract tests with a `FakeSkill` returning fixed text/Delivery → gateway invokes
  `on_inbound`, sender invokes `on_outbound`, `InboundContext` is threaded correctly.
- `test_groq_client.py`: STT and TTS with **mocked** httpx; error and size handling.
- `SkillManager.required_secret_names()`: deduplicated union of `required_secrets` across
  skills.
- `vault_web` allowlist extension: `VaultWebMaintainer`/`configurable_secret_names` with an
  `extra_names` provider includes a skill secret in the writable names; a `POST` of that
  skill secret writes to `FakeVault`; a name in neither `servers.json` nor the skill set →
  `400`; `extra_names=None` reproduces today's behavior (no regression).
- `test_telegram_adapter.py` (extend): voice message → `RawInbound` with attachment;
  `Delivery("voice")` → `send_voice` call (fallback `send_audio`).
- `ari_tools` skill tools: owner-only gating; `skill_enable` on a `needs_secrets` skill
  returns a LAN link.

Slow (`marker slow`, skipped without creds):

- Real Groq round-trip: a small ogg → transcript; a short text → audio bytes.

## 16. Out of scope (this change)

- **Dynamic skill creation from a Telegram request via the coding agent** (Phase 2):
  generating skill code on demand, sandboxing generated code, automatic contract
  validation, and a review-before-activate gate.
- Installing skills from git or a registry.
- Non-owner skill management or per-skill per-user grants.
- **Natural-language skill management** (a subprocess agent tool queuing an intent the main
  process fulfils) — v1 uses slash commands instead (§10).
- OGG/Opus transcoding of TTS output (ffmpeg) and streaming audio.
- `McpProvider` / `CommandHandler` hook types (the contract leaves room; not built now).

## 17. Acceptance criteria

1. Owner runs `/skill_on groq_audio`; the key is missing → Ari replies with the `vault_web`
   `https://<lan-ip>:<port>/v/<token>` link.
2. Opening the link on the LAN shows the form with `GROQ_API_KEY` listed as "falta"
   (because the allowlist was extended with the skill's `required_secrets`); submitting it
   stores it in `vault.enc` (encrypted) and the skill activates on the next reload. The key
   never appears in the Telegram chat or logs.
3. A Telegram voice note is transcribed via Groq and answered by Ari as if typed.
4. When the turn came from voice, Ari's reply is delivered as **text + voice**; if TTS
   fails, text still arrives.
5. `/skill_off groq_audio` stops both hooks without a restart; `/skill_on` restores them
   (hot-reload).
6. A skill that raises on load is isolated (`failed`) and the bot keeps running.
7. Credential intake is the existing `vault_web`: the link is HTTPS-only (a plain-HTTP
   client cannot connect), an expired token returns `403`, and the server is idle-reaped —
   with `GROQ_API_KEY` writable only because the allowlist was extended (a non-allowlisted
   name still returns `400`).
