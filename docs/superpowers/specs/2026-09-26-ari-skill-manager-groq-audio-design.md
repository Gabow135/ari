# Ari — Skill manager & Groq audio skill (Design)

- **Date:** 2026-09-26
- **Status:** Draft — pending user approval
- **Part of:** Extensibility (this: **skill manager + first-party Groq audio skill + LAN credential intake**) → Phase 2 (dynamic skill creation via the coding agent).
- **Builds on:** Secrets vault (`SecretVault`, `FernetVault`, resolution order vault → env → `.env`), `McpRegistry` hot-reload-by-mtime pattern, `Authorizer.is_owner`, Telegram gateway (`TelegramAdapter`, `_on_message`, `_dispatch`), `HandleMessage`, agent tools (`ari_tools.py`).

## 1. Purpose

Give Ari a **skill manager**: a subsystem where owner-only **code plugins** add new
abilities through small, typed hook contracts, with each plugin's credentials stored
**encrypted in the vault** — never in the Telegram chat and never in plaintext on disk.

The first concrete skill is **Groq audio** (bidirectional voice): a Telegram voice note
is transcribed to text before Ari's turn, and Ari's reply can be spoken back as a voice
message.

The owner drives everything **from Telegram** in natural language ("activá el skill de
audio"). When a skill needs a credential, Ari does **not** ask for it in the chat. It
starts a short-lived **LAN-only web form**, sends the owner a link with the machine's LAN
IP, the owner enters the API key in that form, the value is stored encrypted in the vault,
and the skill activates.

Success: the owner asks Ari (by Telegram) to enable the Groq audio skill; Ari replies with
a `http://<lan-ip>:<port>/s/<token>` link; the owner opens it on the same network, pastes
`GROQ_API_KEY`; from then on voice notes are transcribed and Ari can reply with voice —
with the key encrypted in `vault.enc`, absent from the chat history, and absent from logs.

## 2. Locked decisions

| Decision | Choice | Rationale |
|---|---|---|
| What is a skill | **Code plugin**: a local dir with `skill.json` + `skill.py` | User choice; matches the coding-agent generation path (Phase 2) |
| Origin | **Local only**, owner-authored / Ari-authored; **no** git/registry install | User choice; smallest trust surface for a personal bot |
| Execution | **In-process**, first-party skills only in v1 (no sandbox yet) | Only trusted (owner/Ari-written) code runs; sandbox is justified in Phase 2 |
| Management surface | **From Telegram**, natural language → owner-only agent tools | User choice ("yo pido a Ari por telegram") |
| Credential intake | **LAN-only web form**, one-time token, short TTL, ephemeral server | Keeps secrets out of the Telegram chat/logs; the correct channel for a secret |
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

## 8. Credential intake (LAN web form)

`infrastructure/skills/intake_server.py` — `CredentialIntakeServer`.

This is the only new externally-reachable surface, so it is deliberately small and
locked down.

- **Trigger:** when the owner asks to enable a skill in `needs_secrets`, the management
  tool calls `intake.request(skill_name, missing_secrets)`, which mints a one-time token
  and returns a URL `http://<lan-ip>:<port>/s/<token>`. Ari sends that URL by Telegram.
- **LAN IP detection:** open a UDP socket, `connect(("10.255.255.255", 1))` (sends
  nothing), read `getsockname()[0]`. Overridable with `ARI_LAN_HOST`. If detection fails,
  fall back to `127.0.0.1` and warn (owner must be on the same machine).
- **Bind:** to the detected LAN IP (not `0.0.0.0`, never the public interface) on
  `ARI_SKILL_INTAKE_PORT` (default `8770`).
- **Token store:** in-memory only, `{token: IntakeRequest(skill, secrets, expiry)}`.
  Single use, TTL `ARI_SKILL_INTAKE_TTL` (default `600` s), high entropy
  (`secrets.token_urlsafe`).
- **Routes:**
  - `GET /s/<token>` → an HTML form with one password field per missing secret. Unknown
    or expired token → generic 404 (no enumeration).
  - `POST /s/<token>` → read the field values, `FernetVault.set(name, value)` for each,
    **burn the token**, trigger a `SkillManager` reload so the skill activates, and show a
    "listo, ya podés cerrar esta pestaña" page. Ari confirms activation by Telegram.
- **Lifecycle:** started on demand; stopped once there are no pending (unexpired) tokens.
  It does not stay listening between requests.
- **Implementation:** stdlib `http.server.ThreadingHTTPServer` on a daemon thread (no new
  dependency); the request handler calls back into the vault + manager. (aiohttp is a
  possible alternative but adds a dependency.)
- **HTTPS:** v1 ships plain HTTP on the LAN. If the LAN is untrusted, a self-signed
  certificate is an opt-in (`ARI_SKILL_INTAKE_TLS`) — noted, not built in v1.

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

## 10. Management from Telegram (owner-only agent tools)

Skill management is exposed as **owner-only agent tools** (like the existing schedule/admin
tools in `ari_tools.py`), so natural-language requests work:

- `skill_list()` → status of all skills.
- `skill_enable(name)` / `skill_disable(name)` → flip `enabled`, reload. If enabling a
  skill in `needs_secrets`, automatically call the intake flow and return the LAN link.
- `skill_request_secret(name)` → (re)issue the LAN link for a skill's missing secrets.

All are gated by `Authorizer.is_owner`; non-owners cannot see or manage skills.

## 11. Configuration (additions)

| Variable | Default | Purpose |
|---|---|---|
| `ARI_SKILLS_DIR` | `./skills` | Where local skill plugins live |
| `ARI_SKILL_INTAKE_PORT` | `8770` | LAN credential-intake server port |
| `ARI_SKILL_INTAKE_TTL` | `600` | One-time token TTL (seconds) |
| `ARI_LAN_HOST` | auto-detect | Override the LAN bind IP |
| `ARI_SKILL_INTAKE_TLS` | off | (Noted) opt-in self-signed HTTPS for the intake form |

`GROQ_API_KEY` is **not** a `Settings` field — it lives in the vault and is read only by
the skill via `ctx.secret`. `Settings` gains `skills_dir`, `skill_intake_port`,
`skill_intake_ttl`, `lan_host` (all `ARI_`-prefixed).

## 12. Architecture (new/changed)

```
domain/skills/
  models.py              Attachment, RawInbound, Delivery, InboundContext
  contracts.py           SkillContext, InboundTransform, OutboundTransform (Protocols)
application/skills/
  skill_manager.py       discover, validate secrets (vault→env→.env), load, expose hooks, hot-reload
infrastructure/skills/
  skill_loader.py        importlib loader (isolated, mockable)
  intake_server.py       CredentialIntakeServer: LAN-bound, one-time token, ephemeral
skills/groq_audio/
  skill.json             manifest (required_secrets: GROQ_API_KEY)
  skill.py               build_skill: on_inbound (STT) + on_outbound (TTS)
  groq_client.py         Groq STT/TTS HTTP client
application/ari_tools.py + skill_list / skill_enable / skill_disable / skill_request_secret (owner-only)
infrastructure/gateway/telegram_adapter.py  voice branch → RawInbound; Delivery → send_voice
config/settings.py       + skills_dir, skill_intake_port, skill_intake_ttl, lan_host
main.py (build)          construct SkillManager + CredentialIntakeServer; inject into gateway & sender
```

No new hard dependency for the manager or intake (stdlib `http.server`). The Groq client
uses the existing HTTP stack (`httpx`, already present via PTB).

## 13. Security boundary (honest)

- **Secrets never enter the Telegram chat or logs.** They are entered in a LAN form and
  stored with `FernetVault.set()`.
- **Intake server** is LAN-bound, one-time-token, short-TTL, and ephemeral. The main
  residual risk is an untrusted/shared LAN with plain HTTP; `ARI_SKILL_INTAKE_TLS` is the
  documented mitigation. Misbinding to `0.0.0.0` is prevented by design (explicit LAN IP).
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
- Intake token unknown/expired → generic 404; expired tokens are swept on access.

## 15. Testing (pytest + fakes; `python3 -m pytest`, `pythonpath=src`)

Fast (no network, no Telegram, no real HTTP server):

- `test_skill_manager.py`: discovery of valid/invalid manifests; secret validation against
  a `FakeVault` (active vs `needs_secrets`); hot-reload on mtime change; a load error →
  `failed` and others still active; hook filtering by `enabled`/`owner_only`.
- Contract tests with a `FakeSkill` returning fixed text/Delivery → gateway invokes
  `on_inbound`, sender invokes `on_outbound`, `InboundContext` is threaded correctly.
- `test_groq_client.py`: STT and TTS with **mocked** httpx; error and size handling.
- `test_intake_server.py`: token issue → GET form → POST stores into `FakeVault` → token
  burned → second use is 404; TTL expiry; bind uses the injected LAN host, not `0.0.0.0`;
  posted values never logged.
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
- OGG/Opus transcoding of TTS output (ffmpeg) and streaming audio.
- `McpProvider` / `CommandHandler` hook types (the contract leaves room; not built now).

## 17. Acceptance criteria

1. Owner asks Ari (Telegram) to enable `groq_audio`; the key is missing → Ari replies with
   a `http://<lan-ip>:<port>/s/<token>` link.
2. Opening the link on the LAN shows a form; submitting `GROQ_API_KEY` stores it in
   `vault.enc` (encrypted), burns the token, and activates the skill. The key never
   appears in the Telegram chat or logs.
3. A Telegram voice note is transcribed via Groq and answered by Ari as if typed.
4. When the turn came from voice, Ari's reply is delivered as **text + voice**; if TTS
   fails, text still arrives.
5. Disabling the skill (Telegram) stops both hooks without a restart; re-enabling restores
   them (hot-reload).
6. A skill that raises on load is isolated (`failed`) and the bot keeps running.
7. The intake server binds to the LAN IP (not `0.0.0.0`), a used/expired token returns
   404, and the server stops when no tokens are pending.
