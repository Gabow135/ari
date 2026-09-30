# Natural-language Skill Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the owner enable/disable/list skills by talking to Ari ("activá el skill de visión", "apagá el audio", "¿qué skills tenés?") via owner-only agent tools, instead of `/skill_on` / `/skill_off`.

**Architecture:** Ari's agent tools run in a DB-only MCP subprocess. Enabling a skill is a file edit (`SkillManager.set_enabled` → `skill.json`), which the main-process `SkillManager` hot-reloads by mtime — so a subprocess tool can toggle skills. The subprocess builds a `SkillManager` with a new `load=False` flag (discovers/toggles manifests without importing skill code or the vault) and a `catalog()` method for listing. Three new `AriTools` methods wrap it.

**Tech Stack:** Python 3.11+ (runtime `.venv/bin/python`), pytest + pytest-asyncio (`asyncio_mode=auto`), stdlib json/os. No new dependency.

**Spec:** `docs/superpowers/specs/2026-09-30-ari-skill-nl-management-design.md`

## Global Constraints

- Run tests with **`.venv/bin/python -m pytest`**. `pythonpath=["src"]`; `asyncio_mode="auto"` (async tests need no marker); no `conftest.py`.
- New tools are **owner + CHAT context only** — append their names to `ARI_TOOLS`; they land only in `set(ARI_TOOLS)` (like `proponer_codigo`). Do NOT add them to `_READ` or `_USER_CHAT`.
- `set_enabled` already edits `skill.json` atomically (`os.replace`) and calls `_refresh`; do not change it.
- The subprocess `SkillManager(load=False)` must NOT import skill code (no `importlib` load) and must work with `vault=None`.
- `activar_skill`'s reply must NOT claim the skill is "active" — only "activé el skill X" (the main process decides active vs needs_secrets on reload).
- Keep the `/skill_on` / `/skill_off` / `/skills` slash commands (do not remove).
- User-facing Spanish is NEUTRAL (no voseo) — `test_tone` + project standard.
- Conventional Commit messages. **No AI attribution.**

## Review Focus

- **Non-owner, or a TASK/HEARTBEAT turn, tries to toggle a skill** → `DENIED`, nothing changes. (Task 2 gating test.)
- **Unknown/ambiguous skill name** → Ari lists the real skills; the manifest is unchanged. (Task 2 test.)
- **A skill whose `skill.py` would raise on import** → still listable and toggleable under `load=False` (the subprocess never imports skill code). (Task 1 test.)
- **`activar_skill` reply falsely implying the skill is running** → the message must not claim "active" (the main process validates secrets on reload). (Task 2 test asserts the receipt wording.)
- **Subprocess resolving a different skills dir than the main process (cwd drift)** → `ARI_SKILLS_DIR` is passed in the subprocess env so both resolve the same dir. (Task 3.)

---

### Task 1: `SkillManager` — `load` flag + `catalog()`

**Files:**
- Modify: `src/ari/application/skills/skill_manager.py`
- Test: `tests/application/test_skill_manager_catalog.py`

**Interfaces:**
- Produces: `SkillManager(skills_dir, vault=None, env=None, env_file=".env", loader=load_skill, load=True)` — when `load=False`, discovery + status still run but the `importlib` loader is never called (instances stay `None`). `SkillManager.catalog() -> list[dict]` with keys `name`, `description`, `enabled`, `required_secrets`, read straight from each `skill.json` (no secret resolution, no load).
- Consumes: nothing new.

- [ ] **Step 1: Write the failing tests**

```python
# tests/application/test_skill_manager_catalog.py
import json
import pathlib
import tempfile

from ari.application.skills.skill_manager import SkillManager
from tests.fakes import FakeVault


def _skill(root, name, *, enabled, description="", required=None, body="def build_skill(c): return object()\n"):
    d = root / name
    d.mkdir()
    (d / "skill.json").write_text(json.dumps({
        "name": name, "version": "0.1.0", "enabled": enabled, "owner_only": True,
        "description": description, "entrypoint": "skill.py", "factory": "build_skill",
        "required_secrets": required or [], "hooks": ["inbound_transform"], "config": {},
    }))
    (d / "skill.py").write_text(body)
    return d


def _root():
    return pathlib.Path(tempfile.mkdtemp())


def test_load_false_does_not_import_skill_code():
    root = _root()
    # This skill.py raises on import — with load=False it must NOT be imported.
    _skill(root, "boom", enabled=True, body="raise RuntimeError('must not import')\n")
    m = SkillManager(str(root), vault=FakeVault({}), env={}, load=False)
    statuses = {s.name: s for s in m.list()}
    assert "boom" in statuses          # discovered
    assert statuses["boom"].state == "active"   # enabled + no secrets, but instance not loaded
    # No exception raised means the loader was never called.


def test_catalog_reads_manifest_without_resolving_secrets():
    root = _root()
    _skill(root, "groq_vision", enabled=False, description="Ve imágenes",
           required=["GROQ_API_KEY"])
    _skill(root, "documents", enabled=True, description="Lee documentos")
    m = SkillManager(str(root), vault=None, env={}, load=False)
    cat = {c["name"]: c for c in m.catalog()}
    assert cat["groq_vision"]["enabled"] is False
    assert cat["groq_vision"]["description"] == "Ve imágenes"
    assert cat["groq_vision"]["required_secrets"] == ["GROQ_API_KEY"]
    assert cat["documents"]["enabled"] is True and cat["documents"]["required_secrets"] == []


def test_set_enabled_still_flips_under_load_false():
    root = _root()
    _skill(root, "documents", enabled=False, description="d")
    m = SkillManager(str(root), vault=None, env={}, load=False)
    assert m.set_enabled("documents", True) is True
    assert {c["name"]: c["enabled"] for c in m.catalog()}["documents"] is True
    assert m.set_enabled("nope", True) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/application/test_skill_manager_catalog.py -v`
Expected: FAIL — `__init__() got an unexpected keyword argument 'load'` / no `catalog`.

- [ ] **Step 3: Write minimal implementation**

In `src/ari/application/skills/skill_manager.py`, add the `load` param to `__init__` and store it (place after `loader=load_skill`):

```python
    def __init__(self, skills_dir: str, vault=None, env: dict | None = None,
                 env_file: str = ".env", loader=load_skill, load: bool = True):
        self._dir = skills_dir
        self._vault = vault
        self._env = env if env is not None else os.environ
        self._env_file = env_file
        self._loader = loader
        self._load = load
        self._stamps: tuple | None = None
        self._loaded: list[_Loaded] = []
        self._refresh()
```

In `_rebuild`, replace the enabled+satisfied load block. The current tail of the loop is:

```python
            if missing:
                loaded.append(_Loaded(m, SkillStatus(name, version, True, "needs_secrets", missing, hooks), None))
                continue
            try:
                inst = self._loader(name, skill_dir, m["entrypoint"], m["factory"],
                                    dict(m.get("config", {})))
            except Exception as exc:  # isolate a broken skill
                log.error("skill %s failed to load: %s", name, exc)
                loaded.append(_Loaded(m, SkillStatus(name, version, True, "failed", missing, hooks), None))
                continue
            loaded.append(_Loaded(m, SkillStatus(name, version, True, "active", [], hooks), inst))
```

Replace it with (add the `load=False` short-circuit before the loader):

```python
            if missing:
                loaded.append(_Loaded(m, SkillStatus(name, version, True, "needs_secrets", missing, hooks), None))
                continue
            if not self._load:
                # discovery/toggling only (e.g. the MCP subprocess): mark active but
                # never import the skill's code.
                loaded.append(_Loaded(m, SkillStatus(name, version, True, "active", [], hooks), None))
                continue
            try:
                inst = self._loader(name, skill_dir, m["entrypoint"], m["factory"],
                                    dict(m.get("config", {})))
            except Exception as exc:  # isolate a broken skill
                log.error("skill %s failed to load: %s", name, exc)
                loaded.append(_Loaded(m, SkillStatus(name, version, True, "failed", missing, hooks), None))
                continue
            loaded.append(_Loaded(m, SkillStatus(name, version, True, "active", [], hooks), inst))
```

Add the `catalog` method (near `list`):

```python
    def catalog(self) -> list[dict]:
        """Name/description/enabled/required_secrets straight from each manifest —
        no secret resolution and no code load. Safe for the MCP subprocess."""
        out = []
        for p in self._manifest_paths():
            try:
                with open(p, encoding="utf-8") as f:
                    m = json.load(f)
            except (OSError, ValueError):
                continue
            out.append({
                "name": m.get("name", ""),
                "description": m.get("description", ""),
                "enabled": bool(m.get("enabled", False)),
                "required_secrets": list(m.get("required_secrets", [])),
            })
        return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/application/test_skill_manager_catalog.py -v`
Expected: PASS (3 tests). Also run the existing manager tests to confirm no regression:
Run: `.venv/bin/python -m pytest tests/application/test_skill_manager.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ari/application/skills/skill_manager.py tests/application/test_skill_manager_catalog.py
git commit -m "feat(skills): SkillManager load=False flag and catalog() for subprocess toggling"
```

---

### Task 2: `AriTools` skill tools + permissions

**Files:**
- Modify: `src/ari/application/ari_tools.py` (add `skills` kwarg + 3 methods + `_toggle_skill`)
- Modify: `src/ari/domain/tools/ari_permissions.py` (append 3 tool names to `ARI_TOOLS`)
- Test: `tests/application/test_ari_tools_skills.py`

**Interfaces:**
- Consumes: `SkillManager.catalog()` + `set_enabled` (Task 1); `_allowed`, `_receipt`, `DENIED` (existing).
- Produces: `AriTools.ver_skills()`, `AriTools.activar_skill(nombre)`, `AriTools.desactivar_skill(nombre)`; `"ver_skills"`, `"activar_skill"`, `"desactivar_skill"` in `ARI_TOOLS`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/application/test_ari_tools_skills.py
import json
import pathlib
import tempfile
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from ari.application.ari_tools import DENIED, Actor, AriTools
from ari.application.skills.skill_manager import SkillManager
from ari.domain.tools.ari_permissions import CHAT, HEARTBEAT, TASK
from ari.infrastructure.memory.sqlite_memory_adapter import SqliteMemoryAdapter
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_turn_log import SqliteTurnLog
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore

TZ = ZoneInfo("America/Guayaquil")
NOW = datetime(2026, 9, 30, 15, 0, tzinfo=timezone.utc)


def _skills_dir():
    root = pathlib.Path(tempfile.mkdtemp())
    for name, enabled, desc in [("groq_audio", True, "Escucha audios"),
                                ("groq_vision", False, "Ve imágenes")]:
        d = root / name
        d.mkdir()
        (d / "skill.json").write_text(json.dumps({
            "name": name, "version": "0.1.0", "enabled": enabled, "owner_only": True,
            "description": desc, "entrypoint": "skill.py", "factory": "build_skill",
            "required_secrets": [], "hooks": ["inbound_transform"], "config": {}}))
        (d / "skill.py").write_text("def build_skill(c): return object()\n")
    return root


@pytest.fixture
async def env():
    conn = await connect(":memory:", embedding_dim=4)
    skills = SkillManager(str(_skills_dir()), load=False)
    yield conn, skills, SqliteTurnLog(conn)
    await conn.close()


def _tools(env, owner=True, context=CHAT):
    conn, skills, log = env
    actor = Actor("42", "42", "Gabriel", owner, context, "t1")
    return AriTools(actor, schedule=SqliteScheduleStore(conn),
                    memory=SqliteMemoryAdapter(conn, embedding_dim=4), turn_log=log, tz=TZ,
                    max_items=20, clock=lambda: NOW, skills=skills)


async def test_ver_skills_lists_state_and_description(env):
    out = await _tools(env).ver_skills()
    assert "groq_audio [on]" in out and "Escucha audios" in out
    assert "groq_vision [off]" in out and "Ve imágenes" in out


async def test_activar_skill_flips_manifest_and_writes_receipt(env):
    _, skills, log = env
    out = await _tools(env).activar_skill("groq_vision")
    assert "groq_vision" in out and "activé" in out.lower()
    assert "activo" not in out.lower()   # must not claim it is running
    assert {c["name"]: c["enabled"] for c in skills.catalog()}["groq_vision"] is True
    assert await log.receipts("t1") == [out]


async def test_desactivar_skill_flips_off(env):
    _, skills, _ = env
    await _tools(env).desactivar_skill("groq_audio")
    assert {c["name"]: c["enabled"] for c in skills.catalog()}["groq_audio"] is False


async def test_case_insensitive_name(env):
    _, skills, _ = env
    await _tools(env).activar_skill("GROQ_VISION")
    assert {c["name"]: c["enabled"] for c in skills.catalog()}["groq_vision"] is True


async def test_unknown_name_lists_and_changes_nothing(env):
    _, skills, _ = env
    out = await _tools(env).activar_skill("banana")
    assert "banana" in out and "groq_audio" in out and "groq_vision" in out
    # nothing toggled
    assert {c["name"]: c["enabled"] for c in skills.catalog()} == {"groq_audio": True, "groq_vision": False}


@pytest.mark.parametrize("owner,context", [(False, CHAT), (True, TASK), (True, HEARTBEAT)])
async def test_denied_outside_owner_chat(env, owner, context):
    _, skills, _ = env
    assert await _tools(env, owner, context).activar_skill("groq_vision") == DENIED
    assert await _tools(env, owner, context).ver_skills() == DENIED
    assert {c["name"]: c["enabled"] for c in skills.catalog()}["groq_vision"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/application/test_ari_tools_skills.py -v`
Expected: FAIL — `AriTools.__init__() got an unexpected keyword argument 'skills'`.

- [ ] **Step 3: Write minimal implementation**

In `src/ari/application/ari_tools.py` `__init__`, add the trailing kwarg and store it:

```python
    def __init__(self, actor: Actor, *, schedule, memory, turn_log, tz, max_items: int,
                 clock, gate=None, access=None, coding=None, missions=None, credentials=None,
                 skills=None):
        self._a, self._schedule, self._memory, self._log = actor, schedule, memory, turn_log
        self._tz, self._max, self._clock = tz, max_items, clock
        self._gate, self._access = gate, access
        self._coding = coding  # SqliteCodingRequests | None
        self._missions = missions  # SqliteMissions | None
        self._credentials = credentials  # SqliteCredentialRequests | None
        self._skills = skills  # SkillManager (load=False) | None
```

Add the three methods (near `pedir_credenciales`):

```python
    async def ver_skills(self) -> str:
        if not self._allowed("ver_skills"):
            return DENIED
        if self._skills is None:
            return "No pude verlos: el sistema de skills no está disponible."
        rows = self._skills.catalog()
        if not rows:
            return "No hay skills instalados."
        lines = []
        for s in rows:
            estado = "on" if s["enabled"] else "off"
            secretos = f" (necesita: {', '.join(s['required_secrets'])})" if s["required_secrets"] else ""
            desc = s["description"] or ""
            lines.append(f"• {s['name']} [{estado}]: {desc}{secretos}")
        return "\n".join(lines)

    async def activar_skill(self, nombre: str) -> str:
        return await self._toggle_skill(nombre, True, "Activé")

    async def desactivar_skill(self, nombre: str) -> str:
        return await self._toggle_skill(nombre, False, "Desactivé")

    async def _toggle_skill(self, nombre: str, enabled: bool, verb: str) -> str:
        if not self._allowed("activar_skill" if enabled else "desactivar_skill"):
            return DENIED
        if self._skills is None:
            return "No pude hacerlo: el sistema de skills no está disponible."
        query = (nombre or "").strip()
        names = [c["name"] for c in self._skills.catalog()]
        match = next((n for n in names if n == query), None) \
            or next((n for n in names if n.lower() == query.lower()), None)
        if match is None:
            listado = ", ".join(names) if names else "(ninguno)"
            return f"No encontré un skill «{query}». Tenés: {listado}."
        self._skills.set_enabled(match, enabled)
        extra = " Si le falta la credencial, te mando el link cuando lo uses." if enabled else ""
        return await self._receipt(f"🧩 {verb} el skill «{match}».{extra}")
```

In `src/ari/domain/tools/ari_permissions.py`, append the three names to `ARI_TOOLS` (leave `_READ`/`_USER_CHAT` unchanged → owner+CHAT only):

```python
ARI_TOOLS = ("agendar", "listar_agenda", "cancelar", "recordar_dato", "olvidar_dato",
             "ver_datos", "aprobar_acceso", "revocar_acceso", "ver_accesos",
             "enviar_mensaje", "proponer_codigo",
             "asignar_mision", "ver_misiones", "cancelar_mision",
             "pedir_credenciales",
             "ver_skills", "activar_skill", "desactivar_skill")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/application/test_ari_tools_skills.py tests/domain/test_ari_permissions.py -q`
Expected: PASS. (`test_ari_permissions.py::test_catalogue` asserts the full `ARI_TOOLS` tuple — update its expected tuple to include the three new names if it fails.)

- [ ] **Step 5: Commit**

```bash
git add src/ari/application/ari_tools.py src/ari/domain/tools/ari_permissions.py tests/application/test_ari_tools_skills.py tests/domain/test_ari_permissions.py
git commit -m "feat(skills): owner-only ver_skills/activar_skill/desactivar_skill agent tools"
```

---

### Task 3: Wire tools into the MCP subprocess + pass ARI_SKILLS_DIR (integration)

**Files:**
- Modify: `src/ari/mcp_server/server.py` (register 3 `@tool` wrappers)
- Modify: `src/ari/mcp_server/__main__.py` (construct `SkillManager(load=False)` and pass `skills=`)
- Modify: `src/ari/main.py` (`AriServerSpec` `base_env` += `ARI_SKILLS_DIR`)
- Test: none new (glue); proof is the full suite + import smoke.

**Interfaces:**
- Consumes: `AriTools.ver_skills/activar_skill/desactivar_skill` (Task 2); `SkillManager(load=False)` (Task 1).

- [ ] **Step 1: Register the three `@tool` wrappers in `server.py`**

In `build_server`, next to the `pedir_credenciales` `@tool` block (before `return server`):

```python
    @tool("ver_skills")
    async def ver_skills() -> str:
        """(Solo el creador) Lista los skills instalados con su estado (on/off) y
        descripción. Úsala cuando el creador pregunte qué skills tienes o cuáles
        están activos."""
        return await (await get_tools()).ver_skills()

    @tool("activar_skill")
    async def activar_skill(nombre: str) -> str:
        """(Solo el creador) Activa un skill por su nombre cuando el creador te lo
        pida ("activá/prendé el skill de X"). Si dudas del nombre exacto, primero
        usa ver_skills. nombre: el nombre del skill (p. ej. groq_vision, groq_audio)."""
        return await (await get_tools()).activar_skill(nombre)

    @tool("desactivar_skill")
    async def desactivar_skill(nombre: str) -> str:
        """(Solo el creador) Desactiva un skill por su nombre cuando el creador te lo
        pida ("apagá/desactivá el skill de X"). nombre: el nombre del skill."""
        return await (await get_tools()).desactivar_skill(nombre)
```

- [ ] **Step 2: Construct the SkillManager in `__main__.py`**

In `src/ari/mcp_server/__main__.py`, import `SkillManager` and pass `skills=` to `AriTools(...)`:

```python
from ari.application.skills.skill_manager import SkillManager
```

and in the `AriTools(...)` call, add (alongside `credentials=...`):

```python
            skills=SkillManager(env.get("ARI_SKILLS_DIR", "./skills"), load=False))
```

- [ ] **Step 3: Pass ARI_SKILLS_DIR in the subprocess env (`main.py`)**

In `src/ari/main.py`, add `ARI_SKILLS_DIR` to the `AriServerSpec` base_env dict:

```python
    ari_spec = AriServerSpec(sys.executable, ("-m", "ari.mcp_server"), {
        "ARI_DB_PATH": os.path.abspath(settings.db_path),
        "ARI_TIMEZONE": settings.timezone,
        "ARI_MAX_ITEMS": str(settings.max_items_per_user),
        "ARI_OWNER_IDS": ",".join(sorted(settings.owner_id_set)),
        "ARI_SKILLS_DIR": os.path.abspath(settings.skills_dir),
    })
```

- [ ] **Step 4: Verify the whole suite + import smokes (venv)**

Run: `.venv/bin/python -m pytest -q -m "not slow"`
Expected: all pass, no new failures.

Run: `.venv/bin/python -c "import ari.main"`
Expected: no error.

Run: `.venv/bin/python -c "import ari.mcp_server.__main__"`
Expected: no error (note: `import ari.mcp_server.server` may fail on `mcp.server` under a non-venv Python; under the venv it imports).

- [ ] **Step 5: Manual smoke (documented, not automated)**

1. `./run.sh` (Ari under the venv).
2. In chat (as owner): "¿qué skills tenés?" → Ari lists them with on/off.
3. "activá el skill de visión" → Ari confirms it activated `groq_vision`.
4. Send a photo → it's processed (skill hot-reloaded).
5. "apagá la visión" → a later photo is not processed.
6. As a non-owner, asking to toggle → refused.

- [ ] **Step 6: Commit**

```bash
git add src/ari/mcp_server/server.py src/ari/mcp_server/__main__.py src/ari/main.py
git commit -m "feat(skills): expose skill tools in the MCP subprocess; pass ARI_SKILLS_DIR"
```

---

## Notes for the implementer

- Run everything with `.venv/bin/python`.
- The subprocess `SkillManager(load=False)` never imports skill code and works with `vault=None`; its `list()` state for a vault-backed skill may say `needs_secrets` (it can't see the vault) — that's why `ver_skills` uses `catalog()` (manifest-only: name/description/enabled/required_secrets), not the resolved state. The precise active/needs_secrets state stays available via `/skills` in the main process.
- Enabling takes effect on the NEXT message (main-process mtime reload), not retroactively.
- Keep Spanish neutral (no voseo) so `test_tone` stays green.
