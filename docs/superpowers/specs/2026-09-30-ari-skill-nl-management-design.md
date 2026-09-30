# Ari — Natural-language skill management (Design)

- **Date:** 2026-09-30
- **Status:** Draft — pending user approval
- **Part of:** Skill manager UX (Phase 2 follow-on: manage skills by talking to Ari instead of slash commands).
- **Builds on:** the skill manager (`SkillManager.set_enabled`, hot-reload by mtime, `skill.json` manifests), Ari's agent-tools system (`AriTools`, `ari_permissions.ARI_TOOLS`, `mcp_server/server.py`, `mcp_server/__main__.py`), and the existing `/skill_on` / `/skill_off` slash commands (kept as an alternative).

## 1. Purpose

Let the owner enable/disable skills **by talking to Ari** ("activá el skill de visión",
"apagá el audio", "¿qué skills tenés?") instead of typing `/skill_on` / `/skill_off`.

This is feasible from Ari's agent tools — which run in a **DB-only MCP subprocess** — because
enabling a skill is just editing `skills/<name>/skill.json`'s `enabled` flag
(`SkillManager.set_enabled`, atomic `os.replace`), and the main-process `SkillManager`
hot-reloads by mtime. Unlike minting a credential link (which needs the main process's
in-memory HTTPS server), a **file edit works from the subprocess**.

Success: the owner types "prendé la visión" in normal chat → Ari calls a tool that flips
`groq_vision` on → the next photo is processed. No slash command typed.

## 2. Locked decisions

| Decision | Choice | Rationale |
|---|---|---|
| Surface | **Owner-only agent tools** (LLM-invoked), context `CHAT` | User wants natural language, not commands |
| Scope | **By-name enable/disable + list** (not proactive auto-enable) | User choice; proactive has a flow wrinkle (media dropped pre-turn) |
| Mechanism | `SkillManager.set_enabled` (edit `skill.json`) from the subprocess | A file edit is subprocess-safe; main process hot-reloads by mtime |
| Subprocess SkillManager | Constructed with `load=False` (new flag) | Discover manifests + toggle WITHOUT importlib-loading skill code or needing the vault |
| Listing | `SkillManager.catalog()` (name/description/enabled/required_secrets from manifests) | No secret resolution → accurate + vault-free in the subprocess |
| Name resolution | exact name, then case-insensitive exact; not found/ambiguous → return the list | The LLM maps "visión"→`groq_vision` from the listing; the tool stays deterministic |
| Slash commands | **Kept** (`/skill_on`, `/skill_off`, `/skills`) | Alternative; no removal |
| Enable message | Never claims "active" — only "activé el skill X" | The main process (with the vault) decides active vs needs_secrets on reload |
| Neutral Spanish | All user-facing strings tuteo/neutral (no voseo) | `test_tone` + project standard |

## 3. Agent tools (owner + CHAT only)

New `AriTools` methods (mirroring `proponer_codigo` / `pedir_credenciales`):

```python
async def ver_skills(self) -> str:
    if not self._allowed("ver_skills"):
        return DENIED
    if self._skills is None:
        return "No pude verlos: el sistema de skills no está disponible."
    rows = self._skills.catalog()          # [{name, description, enabled, required_secrets}]
    if not rows:
        return "No hay skills instalados."
    lines = []
    for s in rows:
        estado = "on" if s["enabled"] else "off"
        secretos = f" (necesita: {', '.join(s['required_secrets'])})" if s["required_secrets"] else ""
        lines.append(f"• {s['name']} [{estado}]: {s['description']}{secretos}")
    return "\n".join(lines)

async def activar_skill(self, nombre: str) -> str:
    return await self._toggle_skill(nombre, True, "activé")

async def desactivar_skill(self, nombre: str) -> str:
    return await self._toggle_skill(nombre, False, "desactivé")
```

`_toggle_skill(nombre, enabled, verb)`:
- gate with `_allowed`; if `self._skills is None` → "no disponible".
- resolve `nombre` against `catalog()` names: exact match, else case-insensitive exact; if
  zero matches → return "No encontré un skill «{nombre}». Tenés: {name list}"; if it maps to a
  name, call `self._skills.set_enabled(name, enabled)`.
- return a `_receipt`: `f"🧩 {verb.capitalize()} el skill «{name}»."` (do NOT claim active; if
  the skill declares secrets, append a neutral hint: " Si le falta la credencial, te mando el
  link cuando la uses.").

The `@tool` descriptions in `server.py` teach the LLM to call these when the owner asks to
turn a skill on/off by name or description, or asks what skills exist.

## 4. SkillManager additions

`src/ari/application/skills/skill_manager.py`:

- **`load` flag** on `__init__(self, skills_dir, vault=None, env=None, env_file=".env", loader=load_skill, load=True)`. When `load=False`, `_rebuild` still discovers manifests and computes status, but **skips the importlib `loader(...)` call** (instances stay `None`). This lets the subprocess toggle/list skills without importing skill code (pypdf/httpx/etc.) or needing the vault. `run_inbound`/`run_outbound` in the subprocess would return nothing (no instances) — which is correct, the subprocess never runs transforms.
- **`catalog()`** → `list[dict]` with `name`, `description`, `enabled`, `required_secrets` read straight from each `skill.json` (no secret resolution, no load). Used by `ver_skills`. (`description` comes from the manifest; today's `SkillStatus` omits it, so `catalog()` reads the manifest directly.)

`set_enabled` is unchanged (already edits `skill.json` atomically and calls `_refresh`).

## 5. Wiring

- `AriTools.__init__` gains a trailing `skills=None` kwarg; store `self._skills`.
- `mcp_server/__main__.py` `_get_tools()`: construct `SkillManager(env.get("ARI_SKILLS_DIR", "./skills"), load=False)` and pass `skills=` to `AriTools(...)`. (Import `SkillManager`.)
- `AriServerSpec.base_env` (built in `main.py`): add `"ARI_SKILLS_DIR": settings.skills_dir` so the subprocess resolves the same skills dir regardless of cwd.
- `ari_permissions.ARI_TOOLS`: append `"ver_skills"`, `"activar_skill"`, `"desactivar_skill"`. They land only in `set(ARI_TOOLS)` → **owner + CHAT** only (like `proponer_codigo`).
- `mcp_server/server.py`: register the three `@tool` wrappers with Spanish descriptions guiding when to use them.

## 6. Cross-process behavior

1. Owner (chat): "activá la visión" → the LLM (subprocess) calls `activar_skill("groq_vision")`
   → `set_enabled` edits `skills/groq_vision/skill.json` (`enabled=true`, atomic).
2. The main-process `SkillManager` hot-reloads on its next accessor call (e.g. the next
   `run_inbound` when a photo arrives) → sees `enabled` → validates secrets against the **real
   vault** → active (GROQ_API_KEY already loaded) or `needs_secrets`.
3. If `needs_secrets`, the existing reactive path (`missing_inbound_secrets` → `vault_web`
   link) fires when the owner next sends media. No new credential machinery.

The change takes effect on the **next** message, not retroactively (an enable does not
reprocess an already-sent item). This matches the by-name (non-proactive) scope.

## 7. Error handling

- Unknown/ambiguous name → a message listing the available skills (never a crash).
- `self._skills is None` (misconfig) → a clear "no disponible" message.
- Non-owner or non-CHAT context → `DENIED` (permission table).
- `set_enabled` returns `False` (name not found at write time) → same "no encontré" message.
- The subprocess `SkillManager(load=False)` never imports skill code, so a broken skill's
  import can't affect these tools.

## 8. Architecture (new/changed)

```
src/ari/application/skills/skill_manager.py  + load flag (skip importlib when False); + catalog()
src/ari/application/ari_tools.py             + skills kwarg; + ver_skills/activar_skill/desactivar_skill/_toggle_skill
src/ari/domain/tools/ari_permissions.py      + ver_skills, activar_skill, desactivar_skill in ARI_TOOLS
src/ari/mcp_server/server.py                 + 3 @tool wrappers
src/ari/mcp_server/__main__.py               + skills=SkillManager(ARI_SKILLS_DIR, load=False)
src/ari/main.py                              AriServerSpec base_env += ARI_SKILLS_DIR
```

No new dependency. The `/skill_on` / `/skill_off` / `/skills` slash commands are untouched.

## 9. Testing (pytest + fakes; `.venv/bin/python -m pytest`)

- `SkillManager(load=False)`: discovers manifests, `catalog()` returns name/description/enabled/
  required_secrets, `set_enabled` still flips `skill.json`, and NO skill code is imported (a
  skill whose `skill.py` would raise on import is still listed/toggleable). `list()` instances
  are all `None`.
- `catalog()`: reads description + required_secrets from the manifest without resolving secrets
  (works with `vault=None`).
- `AriTools` tools (mirror `test_ari_tools_credentials.py`): owner+CHAT `activar_skill("groq_audio")`
  flips the manifest via a real `SkillManager(tmp_skills_dir, load=False)` and writes a receipt;
  `desactivar_skill` flips it off; `ver_skills` lists the tmp skills; an unknown name → the
  "no encontré" + list message with nothing changed; non-owner / TASK / HEARTBEAT → `DENIED`
  and no change; case-insensitive name resolution.
- `ari_permissions`: the three tools are in the owner CHAT set, not in user/task/heartbeat sets.

## 10. Out of scope

- Proactive auto-enable (Ari turning a skill on when it infers you need it) — flow wrinkle
  (media is dropped before the LLM turn); deferred.
- Creating/installing NEW skills by conversation (that is the deferred coding-agent path).
- Editing skill `config` (models, prompts) by conversation.
- Removing the slash commands.
- Making the subprocess resolve vault secrets (listing shows required_secrets, not live
  loaded-state; `/skills` in the main process shows the precise active/needs_secrets state).

## 11. Acceptance criteria

1. Owner says "activá el skill de audio" (natural chat) → Ari calls `activar_skill`, flips
   `groq_audio`'s `enabled` to true, and confirms; no slash command used.
2. Sending a voice note afterward works (main process reloaded the skill).
3. "apagá el audio" → `desactivar_skill` flips it off; a later voice note is not transcribed.
4. "¿qué skills tenés?" → Ari lists each skill with its on/off state and description.
5. "activá el skill de banana" (unknown) → Ari replies it doesn't exist and lists the real
   skills; nothing changes.
6. A non-owner (or a scheduled/heartbeat turn) cannot toggle skills (`DENIED`).
7. Enabling a skill whose credential is already in the vault (groq_vision/audio) makes it
   active on the next use; if a secret is missing, the existing `/vault` reactive link still
   fires. No skill code is imported in the MCP subprocess.
