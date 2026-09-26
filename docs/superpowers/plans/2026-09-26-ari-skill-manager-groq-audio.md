# Skill Manager & Groq Audio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Ari an owner-only skill-manager subsystem (local code plugins with typed hooks and vault-backed credentials) plus a first-party bidirectional Groq audio skill (voice→text on the way in, text→voice on the way out).

**Architecture:** Typed hook contracts live in `domain/skills`. A `SkillManager` (application) discovers `skills/<name>/skill.json` plugins, validates their `required_secrets` against the vault (vault→env→.env), loads enabled+satisfied ones via a `SkillLoader`, and exposes `run_inbound`/`run_outbound`. The Telegram gateway transcribes voice through `run_inbound` before the normal turn and renders voice replies through `run_outbound` after. Credential intake reuses the existing `vault_web` maintainer (its writable allowlist is extended with skills' `required_secrets`). Management is owner-only slash commands in the main process.

**Tech Stack:** Python 3.11+, python-telegram-bot 22.8, httpx 0.28.1, cryptography (Fernet + self-signed TLS, already present), stdlib `http.server`/`importlib`/`json`, pytest + pytest-asyncio (`asyncio_mode=auto`).

**Spec:** `docs/superpowers/specs/2026-09-26-ari-skill-manager-groq-audio-design.md`

## Global Constraints

- Python **>= 3.11**. No new hard dependency: reuse `httpx` (0.28.1), `cryptography`, and stdlib.
- Tests run with `python3 -m pytest` (the venv's `pytest`/`pip` point at 3.9 — always use `python3 -m`). `pythonpath = ["src"]`. There is **no `conftest.py`**; import shared fakes from `tests/fakes.py`. Network/model tests get `@pytest.mark.slow`.
- `asyncio_mode = "auto"` — async test functions need no marker.
- Import style is `from ari.<pkg> import <X>` (source root `src/`).
- Skills are **owner-only, in-process, first-party** in v1. `owner_only` defaults to **true** when a manifest omits it.
- Secrets are **never logged** and **never sent to the Telegram chat**. Values are written only via `FernetVault.set()` (through the reused `vault_web` form).
- Groq STT: model `whisper-large-v3-turbo`, `POST https://api.groq.com/openai/v1/audio/transcriptions` (multipart `file` + `model`; accepts `ogg`). Groq TTS: model `canopylabs/orpheus-v1-english`, `POST .../audio/speech` (JSON `model`/`input`/`voice`/`response_format`; returns WAV). Voice/model come from `skill.json` `config`.
- A voice turn is answered with **text + voice**; a TTS failure degrades to **text only**.
- Conventional Commit messages. **No AI attribution / no `Co-Authored-By`.**
- Deviations from the spec adopted here for simplicity (all noted at their task): `SkillContext` exposes `secret()/config/log` only (the spec's `http` convenience is dropped — skills use `httpx` directly); the Groq skill ships as a **single** `skill.py` (client + hooks) to avoid intra-skill sibling imports; `SkillStatus.state` adds a `"disabled"` value (enabled=false, nothing missing) alongside `active`/`needs_secrets`/`failed`.

## Review Focus

- **Voice note when the skill is off or `GROQ_API_KEY` is missing** — today voice is silently dropped; Ari must reply something, not go dark. (Task 6 + Task 8 tests.)
- **Groq STT/TTS network failure or timeout** — inbound falls back to a "couldn't transcribe" reply; outbound falls back to text-only; never crash, never log the audio. (Task 5 tests.)
- **Audio larger than 25 MB** — rejected before hitting Groq (no opaque 413). (Task 5 test.)
- **A malformed `skill.json` or a `skill.py` that raises on load** — isolated as `failed`; other skills stay `active`; the bot keeps running. (Task 3 tests.)
- **`/skill_on <name>` for a non-existent skill, or a non-owner invoking `/skills`** — a clear message / owner refusal, not a stack trace. (Task 7 SkillAdmin test + Task 8 owner-gate.)

---

### Task 1: Skill domain — models & contracts

**Files:**
- Create: `src/ari/domain/skills/__init__.py` (empty)
- Create: `src/ari/domain/skills/models.py`
- Create: `src/ari/domain/skills/contracts.py`
- Test: `tests/domain/test_skills_domain.py`

**Interfaces:**
- Produces: `Attachment(kind:str, mime:str, data:bytes, filename:str="", duration:int|None=None)`, `RawInbound(user_id:str, chat_id:str, text:str|None=None, attachment:Attachment|None=None)`, `Delivery(kind:str, data:bytes|None=None, mime:str|None=None, text:str|None=None)`, `InboundContext(came_from_voice:bool, chat_id:str, user_id:str)`; Protocols `SkillContext` (`secret(name)->str`, `config` prop, `log` prop), `InboundTransform` (`async on_inbound(raw, ctx)->str|None`), `OutboundTransform` (`async on_outbound(reply, origin, ctx)->list[Delivery]|None`).

- [ ] **Step 1: Write the failing test**

```python
# tests/domain/test_skills_domain.py
import dataclasses
import pytest

from ari.domain.skills.models import Attachment, RawInbound, Delivery, InboundContext
from ari.domain.skills.contracts import InboundTransform, OutboundTransform


def test_models_are_frozen_with_sane_defaults():
    a = Attachment(kind="voice", mime="audio/ogg", data=b"x")
    assert a.filename == "" and a.duration is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.kind = "audio"
    r = RawInbound(user_id="1", chat_id="2")
    assert r.text is None and r.attachment is None
    d = Delivery(kind="voice", data=b"w", mime="audio/wav")
    assert d.text is None


class _DuckSkill:
    async def on_inbound(self, raw, ctx):
        return "hi"

    async def on_outbound(self, reply, origin, ctx):
        return None


def test_protocols_match_a_duck_typed_skill():
    s = _DuckSkill()
    assert isinstance(s, InboundTransform)
    assert isinstance(s, OutboundTransform)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/domain/test_skills_domain.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ari.domain.skills'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ari/domain/skills/__init__.py
```

```python
# src/ari/domain/skills/models.py
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Attachment:
    kind: str            # "voice" | "audio"
    mime: str            # e.g. "audio/ogg"
    data: bytes
    filename: str = ""
    duration: int | None = None


@dataclass(frozen=True, slots=True)
class RawInbound:
    user_id: str
    chat_id: str
    text: str | None = None
    attachment: Attachment | None = None


@dataclass(frozen=True, slots=True)
class Delivery:
    kind: str            # "voice" | "audio" | "text"
    data: bytes | None = None
    mime: str | None = None
    text: str | None = None


@dataclass(frozen=True, slots=True)
class InboundContext:
    came_from_voice: bool
    chat_id: str
    user_id: str
```

```python
# src/ari/domain/skills/contracts.py
from typing import Protocol, runtime_checkable

from ari.domain.ports.gateway_port import OutgoingMessage
from ari.domain.skills.models import Delivery, InboundContext, RawInbound


class SkillContext(Protocol):
    def secret(self, name: str) -> str: ...
    @property
    def config(self) -> dict: ...
    @property
    def log(self): ...


@runtime_checkable
class InboundTransform(Protocol):
    async def on_inbound(self, raw: RawInbound, ctx: SkillContext) -> str | None: ...


@runtime_checkable
class OutboundTransform(Protocol):
    async def on_outbound(
        self, reply: OutgoingMessage, origin: InboundContext, ctx: SkillContext
    ) -> list[Delivery] | None: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/domain/test_skills_domain.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ari/domain/skills tests/domain/test_skills_domain.py
git commit -m "feat(skills): skill domain models and hook contracts"
```

---

### Task 2: SkillLoader & `skills_dir` setting

**Files:**
- Create: `src/ari/infrastructure/skills/__init__.py` (empty)
- Create: `src/ari/infrastructure/skills/skill_loader.py`
- Modify: `src/ari/config/settings.py` (add one field)
- Test: `tests/infrastructure/test_skill_loader.py`

**Interfaces:**
- Produces: `load_skill(name:str, skill_dir:str, entrypoint:str, factory:str, config:dict) -> object` (imports `<skill_dir>/<entrypoint>` under a unique module name and calls `<factory>(config)`; raises on any error). `Settings.skills_dir: str = "./skills"` (env `ARI_SKILLS_DIR`).
- Consumes: nothing.

- [ ] **Step 1: Write the failing test**

```python
# tests/infrastructure/test_skill_loader.py
import textwrap

import pytest

from ari.config.settings import Settings
from ari.infrastructure.skills.skill_loader import load_skill


def test_load_skill_calls_factory_with_config(tmp_path):
    (tmp_path / "skill.py").write_text(textwrap.dedent("""
        class S:
            def __init__(self, config):
                self.config = config
        def build_skill(config):
            return S(config)
    """))
    s = load_skill("t", str(tmp_path), "skill.py", "build_skill", {"k": 1})
    assert s.config == {"k": 1}


def test_load_skill_propagates_errors(tmp_path):
    (tmp_path / "skill.py").write_text("raise RuntimeError('boom')\n")
    with pytest.raises(RuntimeError):
        load_skill("t", str(tmp_path), "skill.py", "build_skill", {})


def test_settings_default_skills_dir(monkeypatch):
    monkeypatch.setenv("ARI_TELEGRAM_BOT_TOKEN", "x")
    assert Settings().skills_dir == "./skills"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/infrastructure/test_skill_loader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ari.infrastructure.skills'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ari/infrastructure/skills/__init__.py
```

```python
# src/ari/infrastructure/skills/skill_loader.py
import importlib.util
import os


def load_skill(name: str, skill_dir: str, entrypoint: str, factory: str, config: dict):
    """Import <skill_dir>/<entrypoint> under a unique module name and call <factory>(config).

    Raises ImportError/AttributeError/anything the module raises — the caller
    (SkillManager) isolates failures so one broken skill cannot take down Ari.
    """
    path = os.path.join(skill_dir, entrypoint)
    spec = importlib.util.spec_from_file_location(f"ari_skill_{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load skill module at {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    build = getattr(module, factory)
    return build(config)
```

Add to `src/ari/config/settings.py`, alongside the other path fields (e.g. after `mcp_config`):

```python
    skills_dir: str = "./skills"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/infrastructure/test_skill_loader.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ari/infrastructure/skills tests/infrastructure/test_skill_loader.py src/ari/config/settings.py
git commit -m "feat(skills): file-based skill loader and skills_dir setting"
```

---

### Task 3: SkillManager

**Files:**
- Create: `src/ari/application/skills/__init__.py` (empty)
- Create: `src/ari/application/skills/skill_manager.py`
- Test: `tests/application/test_skill_manager.py`

**Interfaces:**
- Consumes: `load_skill` (Task 2); a `SecretVault` (`FakeVault` in tests); `dotenv_values` from `dotenv`.
- Produces:
  - `SkillStatus(name:str, version:str, enabled:bool, state:str, missing_secrets:list[str], hooks:list[str])` — `state ∈ {"active","needs_secrets","failed","disabled"}`.
  - `SkillManager(skills_dir:str, vault=None, env:dict|None=None, env_file:str=".env", loader=load_skill)`.
  - `.required_secret_names() -> list[str]`, `.list() -> list[SkillStatus]`, `.set_enabled(name:str, enabled:bool) -> bool`, `async .run_inbound(raw:RawInbound, is_owner:bool) -> str|None`, `async .run_outbound(reply:OutgoingMessage, origin:InboundContext, is_owner:bool) -> list[Delivery]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/application/test_skill_manager.py
import json
import textwrap

from ari.application.skills.skill_manager import SkillManager
from ari.domain.skills.models import Attachment, InboundContext, RawInbound
from ari.domain.ports.gateway_port import OutgoingMessage
from tests.fakes import FakeVault


def _write_skill(root, name, *, enabled=True, required=None, body=None, owner_only=True):
    d = root / name
    d.mkdir()
    (d / "skill.json").write_text(json.dumps({
        "name": name, "version": "0.1.0", "enabled": enabled, "owner_only": owner_only,
        "entrypoint": "skill.py", "factory": "build_skill",
        "required_secrets": required or [], "hooks": ["inbound_transform", "outbound_transform"],
        "config": {"tag": name},
    }))
    (d / "skill.py").write_text(body or textwrap.dedent("""
        from ari.domain.skills.models import Delivery
        class S:
            def __init__(self, config): self.config = config
            async def on_inbound(self, raw, ctx):
                return f"transcript::{ctx.config['tag']}"
            async def on_outbound(self, reply, origin, ctx):
                return [Delivery(kind="voice", data=b"WAV", mime="audio/wav")]
        def build_skill(config): return S(config)
    """))
    return d


def test_active_when_secret_present_and_runs_inbound():
    import tempfile, pathlib
    root = pathlib.Path(tempfile.mkdtemp())
    _write_skill(root, "a", required=["A_KEY"])
    m = SkillManager(str(root), vault=FakeVault({"A_KEY": "v"}), env={})
    statuses = {s.name: s for s in m.list()}
    assert statuses["a"].state == "active"


async def test_run_inbound_returns_transcript():
    import tempfile, pathlib
    root = pathlib.Path(tempfile.mkdtemp())
    _write_skill(root, "a", required=["A_KEY"])
    m = SkillManager(str(root), vault=FakeVault({"A_KEY": "v"}), env={})
    raw = RawInbound(user_id="1", chat_id="2",
                     attachment=Attachment(kind="voice", mime="audio/ogg", data=b"x"))
    assert await m.run_inbound(raw, is_owner=True) == "transcript::a"


def test_missing_secret_is_needs_secrets_and_not_loaded():
    import tempfile, pathlib
    root = pathlib.Path(tempfile.mkdtemp())
    _write_skill(root, "a", required=["A_KEY"])
    m = SkillManager(str(root), vault=FakeVault({}), env={})
    s = m.list()[0]
    assert s.state == "needs_secrets" and s.missing_secrets == ["A_KEY"]


def test_required_secret_names_are_unioned_and_deduped():
    import tempfile, pathlib
    root = pathlib.Path(tempfile.mkdtemp())
    _write_skill(root, "a", required=["A_KEY", "SHARED"])
    _write_skill(root, "b", required=["SHARED", "B_KEY"])
    m = SkillManager(str(root), vault=FakeVault({}), env={})
    assert m.required_secret_names() == ["A_KEY", "B_KEY", "SHARED"]


def test_broken_skill_is_isolated_as_failed():
    import tempfile, pathlib
    root = pathlib.Path(tempfile.mkdtemp())
    _write_skill(root, "ok")
    _write_skill(root, "bad", body="raise RuntimeError('boom')\n")
    m = SkillManager(str(root), vault=FakeVault({}), env={})
    states = {s.name: s.state for s in m.list()}
    assert states["ok"] == "active"
    assert states["bad"] == "failed"


async def test_owner_only_skill_hidden_from_non_owner():
    import tempfile, pathlib
    root = pathlib.Path(tempfile.mkdtemp())
    _write_skill(root, "a", owner_only=True)
    m = SkillManager(str(root), vault=FakeVault({}), env={})
    raw = RawInbound(user_id="1", chat_id="2",
                     attachment=Attachment(kind="voice", mime="audio/ogg", data=b"x"))
    assert await m.run_inbound(raw, is_owner=False) is None
    assert await m.run_inbound(raw, is_owner=True) == "transcript::a"


async def test_secret_scoping_rejects_undeclared():
    import tempfile, pathlib
    body = textwrap.dedent("""
        class S:
            def __init__(self, config): pass
            async def on_inbound(self, raw, ctx):
                return ctx.secret("OTHER")   # not declared -> PermissionError
        def build_skill(config): return S(config)
    """)
    root = pathlib.Path(tempfile.mkdtemp())
    _write_skill(root, "a", required=["A_KEY"], body=body)
    m = SkillManager(str(root), vault=FakeVault({"A_KEY": "v", "OTHER": "z"}), env={})
    raw = RawInbound(user_id="1", chat_id="2",
                     attachment=Attachment(kind="voice", mime="audio/ogg", data=b"x"))
    import pytest
    with pytest.raises(PermissionError):
        await m.run_inbound(raw, is_owner=True)


def test_set_enabled_flips_manifest_and_reloads():
    import tempfile, pathlib
    root = pathlib.Path(tempfile.mkdtemp())
    _write_skill(root, "a", enabled=True)
    m = SkillManager(str(root), vault=FakeVault({}), env={})
    assert m.list()[0].enabled is True
    assert m.set_enabled("a", False) is True
    assert m.list()[0].enabled is False
    assert m.set_enabled("nope", True) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/application/test_skill_manager.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ari.application.skills'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ari/application/skills/__init__.py
```

```python
# src/ari/application/skills/skill_manager.py
import json
import logging
import os
from dataclasses import dataclass

from dotenv import dotenv_values

from ari.infrastructure.skills.skill_loader import load_skill

log = logging.getLogger("ari.skills")

_REQUIRED_KEYS = ("name", "entrypoint", "factory")


@dataclass(frozen=True)
class SkillStatus:
    name: str
    version: str
    enabled: bool
    state: str          # active | needs_secrets | failed | disabled
    missing_secrets: list[str]
    hooks: list[str]


@dataclass
class _Loaded:
    manifest: dict
    status: SkillStatus
    instance: object | None


class _Ctx:
    """Per-skill runtime context; secret() is scoped to declared required_secrets."""

    def __init__(self, allowed: set[str], resolve, config: dict):
        self._allowed, self._resolve, self._config = allowed, resolve, config

    def secret(self, name: str) -> str:
        if name not in self._allowed:
            raise PermissionError(f"skill did not declare secret {name}")
        value = self._resolve(name)
        if value is None:
            raise KeyError(name)
        return value

    @property
    def config(self) -> dict:
        return self._config

    @property
    def log(self):
        return log


class SkillManager:
    def __init__(self, skills_dir: str, vault=None, env: dict | None = None,
                 env_file: str = ".env", loader=load_skill):
        self._dir = skills_dir
        self._vault = vault
        self._env = env if env is not None else os.environ
        self._env_file = env_file
        self._loader = loader
        self._stamps: tuple | None = None
        self._loaded: list[_Loaded] = []
        self._refresh()

    # secret resolution: vault -> env -> .env
    def _resolve(self, name: str) -> str | None:
        if self._vault is not None:
            v = self._vault.get(name)
            if v:
                return v
        if name in self._env:
            return self._env[name]
        return dotenv_values(self._env_file).get(name)

    def _manifest_paths(self) -> list[str]:
        try:
            entries = sorted(os.listdir(self._dir))
        except OSError:
            return []
        out = []
        for e in entries:
            p = os.path.join(self._dir, e, "skill.json")
            if os.path.isfile(p):
                out.append(p)
        return out

    def _stamp(self) -> tuple:
        stamps = []
        for p in self._manifest_paths():
            try:
                st = os.stat(p)
            except OSError:
                continue
            stamps.append((p, st.st_mtime_ns, st.st_size))
        return tuple(stamps)

    def _refresh(self) -> None:
        stamp = self._stamp()
        if stamp == self._stamps:
            return
        self._rebuild()
        self._stamps = stamp

    def _rebuild(self) -> None:
        loaded: list[_Loaded] = []
        for p in self._manifest_paths():
            skill_dir = os.path.dirname(p)
            try:
                with open(p, encoding="utf-8") as f:
                    m = json.load(f)
                for k in _REQUIRED_KEYS:
                    if k not in m:
                        raise ValueError(f"missing '{k}'")
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                log.error("skill manifest %s invalid: %s", p, exc)
                continue

            name, version = m["name"], str(m.get("version", "0"))
            hooks = list(m.get("hooks", []))
            required = list(m.get("required_secrets", []))
            missing = [s for s in required if not self._resolve(s)]
            enabled = bool(m.get("enabled", False))

            if not enabled:
                state = "needs_secrets" if missing else "disabled"
                loaded.append(_Loaded(m, SkillStatus(name, version, False, state, missing, hooks), None))
                continue
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
        self._loaded = loaded

    def _ctx_for(self, m: dict) -> _Ctx:
        return _Ctx(set(m.get("required_secrets", [])), self._resolve, dict(m.get("config", {})))

    def required_secret_names(self) -> list[str]:
        self._refresh()
        names: set[str] = set()
        for l in self._loaded:
            names.update(l.manifest.get("required_secrets", []))
        return sorted(names)

    def list(self) -> list[SkillStatus]:
        self._refresh()
        return [l.status for l in self._loaded]

    def _active(self, is_owner: bool):
        self._refresh()
        for l in self._loaded:
            if l.instance is None:
                continue
            if l.manifest.get("owner_only", True) and not is_owner:
                continue
            yield l

    async def run_inbound(self, raw, is_owner: bool) -> str | None:
        for l in self._active(is_owner):
            if not hasattr(l.instance, "on_inbound"):
                continue
            out = await l.instance.on_inbound(raw, self._ctx_for(l.manifest))
            if out is not None:
                return out
        return None

    async def run_outbound(self, reply, origin, is_owner: bool) -> list:
        deliveries: list = []
        for l in self._active(is_owner):
            if not hasattr(l.instance, "on_outbound"):
                continue
            out = await l.instance.on_outbound(reply, origin, self._ctx_for(l.manifest))
            if out:
                deliveries.extend(out)
        return deliveries

    def set_enabled(self, name: str, enabled: bool) -> bool:
        for p in self._manifest_paths():
            try:
                with open(p, encoding="utf-8") as f:
                    m = json.load(f)
            except (OSError, ValueError):
                continue
            if m.get("name") == name:
                m["enabled"] = enabled
                tmp = p + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(m, f, indent=2)
                os.replace(tmp, p)
                self._refresh()
                return True
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/application/test_skill_manager.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add src/ari/application/skills tests/application/test_skill_manager.py
git commit -m "feat(skills): SkillManager with discovery, secret scoping, hot-reload, isolation"
```

---

### Task 4: Extend `vault_web` allowlist with skill secrets

**Files:**
- Modify: `src/ari/infrastructure/vault_web/maintainer.py`
- Test: `tests/infrastructure/test_vault_web_extra_names.py`

**Interfaces:**
- Consumes: `configurable_secret_names` (existing), `SkillManager.required_secret_names` (Task 3, wired in Task 8).
- Produces: `VaultWebMaintainer(..., extra_names: Callable[[], list[str]] | None = None)`; internal `_writable_names() -> list[str]` = deduped union of `configurable_secret_names(servers_json)` and `extra_names()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/infrastructure/test_vault_web_extra_names.py
from ari.infrastructure.vault_web.maintainer import VaultWebMaintainer
from tests.fakes import FakeVault


def _maintainer(tmp_path, extra=None):
    sj = tmp_path / "servers.json"
    sj.write_text('{"mcpServers": {"g": {"env": {"X": "${FOO}"}}}}')
    return VaultWebMaintainer(
        FakeVault(), str(sj), cert_dir=str(tmp_path), port=0, bind="127.0.0.1",
        ttl_minutes=10, extra_names=extra)


def test_writable_names_without_extra_matches_config(tmp_path):
    m = _maintainer(tmp_path)
    assert m._writable_names() == ["FOO"]


def test_writable_names_union_dedup(tmp_path):
    m = _maintainer(tmp_path, extra=lambda: ["GROQ_API_KEY", "FOO"])
    assert m._writable_names() == ["FOO", "GROQ_API_KEY"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/infrastructure/test_vault_web_extra_names.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'extra_names'`

- [ ] **Step 3: Write minimal implementation**

In `src/ari/infrastructure/vault_web/maintainer.py`, add `extra_names` to `__init__` and use a new `_writable_names()` in `new_link()`:

```python
    def __init__(self, vault, servers_json: str, cert_dir: str, port: int, bind: str,
                 ttl_minutes: int, clock=time.monotonic, extra_names=None):
        self._vault = vault
        self._servers_json = servers_json
        self._cert_dir, self._port, self._bind = cert_dir, port, bind
        self._store = VaultLinkStore(ttl_minutes * 60, clock)
        self._server: VaultWebServer | None = None
        self._lan_ip: str | None = None
        self._lock = threading.Lock()
        self._extra_names = extra_names

    def _writable_names(self) -> list[str]:
        names = configurable_secret_names(self._servers_json)
        if self._extra_names is not None:
            names = sorted(set(names) | set(self._extra_names()))
        return names
```

And in `new_link()` replace the `names = configurable_secret_names(self._servers_json)` line with:

```python
                names = self._writable_names()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/infrastructure/test_vault_web_extra_names.py tests/infrastructure -k vault_web -v`
Expected: PASS (and no regression in existing vault_web tests)

- [ ] **Step 5: Commit**

```bash
git add src/ari/infrastructure/vault_web/maintainer.py tests/infrastructure/test_vault_web_extra_names.py
git commit -m "feat(vault_web): optional extra_names provider to allowlist skill secrets"
```

---

### Task 5: Groq audio skill (single file)

**Files:**
- Create: `skills/groq_audio/skill.json`
- Create: `skills/groq_audio/skill.py` (contains `GroqClient`, `GroqAudioSkill`, `build_skill`)
- Test: `tests/skills/test_groq_audio_skill.py` (+ create `tests/skills/__init__.py` if the suite needs it — it does not, there is no `conftest.py`; a plain dir is fine)

**Interfaces:**
- Consumes: `ari.domain.skills.models.Delivery`; `SkillContext` duck type (`secret`, `config`, `log`); `httpx`.
- Produces (module attributes, loaded by importlib): `GroqClient(api_key, *, base_url=..., timeout=60.0, transport=None)` with `async transcribe(audio, *, model, filename="voice.ogg", language=None)->str` and `async speak(text, *, model, voice, response_format="wav")->bytes`; `GroqAudioSkill(config)` with `on_inbound`/`on_outbound`; `build_skill(config)->GroqAudioSkill`.

Note (deviation): shipped as one `skill.py` so `on_inbound`/`on_outbound` and the client live together without intra-skill sibling imports. `enabled` defaults **false**; the owner turns it on with `/skill_on groq_audio` (Task 8).

- [ ] **Step 1: Write the failing tests**

```python
# tests/skills/test_groq_audio_skill.py
import importlib.util
import logging
import pathlib

import httpx

from ari.domain.skills.models import Attachment, InboundContext, RawInbound
from ari.domain.ports.gateway_port import OutgoingMessage


def _mod():
    p = pathlib.Path("skills/groq_audio/skill.py")
    spec = importlib.util.spec_from_file_location("groq_audio_skill_under_test", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Ctx:
    def __init__(self, key="k"):
        self._key = key
        self.config = {}
        self.log = logging.getLogger("test")

    def secret(self, name):
        return self._key


async def test_transcribe_posts_to_groq_stt():
    def handler(request):
        assert request.url.path == "/openai/v1/audio/transcriptions"
        assert request.headers["authorization"] == "Bearer k"
        return httpx.Response(200, json={"text": "hola"})
    client = _mod().GroqClient("k", transport=httpx.MockTransport(handler))
    assert await client.transcribe(b"\x00\x01", model="whisper-large-v3-turbo") == "hola"


async def test_speak_returns_audio_bytes():
    def handler(request):
        assert request.url.path == "/openai/v1/audio/speech"
        return httpx.Response(200, content=b"WAVDATA")
    client = _mod().GroqClient("k", transport=httpx.MockTransport(handler))
    out = await client.speak("hola", model="canopylabs/orpheus-v1-english", voice="troy")
    assert out == b"WAVDATA"


async def test_on_inbound_returns_transcript(monkeypatch):
    mod = _mod()
    class FakeClient:
        def __init__(self, *a, **k): pass
        async def transcribe(self, audio, **k): return "hola mundo"
    monkeypatch.setattr(mod, "GroqClient", FakeClient)
    skill = mod.build_skill({"stt_model": "whisper-large-v3-turbo"})
    raw = RawInbound(user_id="1", chat_id="2",
                     attachment=Attachment(kind="voice", mime="audio/ogg", data=b"x"))
    assert await skill.on_inbound(raw, _Ctx()) == "hola mundo"


async def test_on_inbound_rejects_oversize(monkeypatch):
    mod = _mod()
    class Boom:
        def __init__(self, *a, **k): pass
        async def transcribe(self, *a, **k): raise AssertionError("must not call Groq")
    monkeypatch.setattr(mod, "GroqClient", Boom)
    skill = mod.build_skill({})
    big = Attachment(kind="voice", mime="audio/ogg", data=b"0" * (25 * 1024 * 1024 + 1))
    raw = RawInbound(user_id="1", chat_id="2", attachment=big)
    assert await skill.on_inbound(raw, _Ctx()) is None


async def test_on_inbound_stt_failure_returns_none(monkeypatch):
    mod = _mod()
    class FailClient:
        def __init__(self, *a, **k): pass
        async def transcribe(self, *a, **k): raise httpx.ConnectError("down")
    monkeypatch.setattr(mod, "GroqClient", FailClient)
    skill = mod.build_skill({})
    raw = RawInbound(user_id="1", chat_id="2",
                     attachment=Attachment(kind="voice", mime="audio/ogg", data=b"x"))
    assert await skill.on_inbound(raw, _Ctx()) is None


async def test_on_outbound_voice_only_when_from_voice(monkeypatch):
    mod = _mod()
    class FakeClient:
        def __init__(self, *a, **k): pass
        async def speak(self, text, **k): return b"WAV"
    monkeypatch.setattr(mod, "GroqClient", FakeClient)
    skill = mod.build_skill({"reply_with_voice": True})
    out = OutgoingMessage(chat_id="2", text="hola")
    voiced = await skill.on_outbound(out, InboundContext(True, "2", "1"), _Ctx())
    assert voiced and voiced[0].kind == "voice" and voiced[0].data == b"WAV"
    assert await skill.on_outbound(out, InboundContext(False, "2", "1"), _Ctx()) is None


async def test_on_outbound_tts_failure_degrades_to_text(monkeypatch):
    mod = _mod()
    class FailClient:
        def __init__(self, *a, **k): pass
        async def speak(self, *a, **k): raise httpx.ConnectError("down")
    monkeypatch.setattr(mod, "GroqClient", FailClient)
    skill = mod.build_skill({"reply_with_voice": True})
    out = OutgoingMessage(chat_id="2", text="hola")
    assert await skill.on_outbound(out, InboundContext(True, "2", "1"), _Ctx()) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/skills/test_groq_audio_skill.py -v`
Expected: FAIL — `FileNotFoundError`/`spec is None` (skill.py does not exist yet)

- [ ] **Step 3: Write minimal implementation**

```json
// skills/groq_audio/skill.json
{
  "name": "groq_audio",
  "version": "0.1.0",
  "enabled": false,
  "owner_only": true,
  "description": "Transcribe voice notes and reply with voice via Groq.",
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

```python
# skills/groq_audio/skill.py
import httpx

from ari.domain.skills.models import Delivery

_BASE = "https://api.groq.com/openai/v1"
_MAX_BYTES = 25 * 1024 * 1024  # Groq free-tier transcription limit


class GroqClient:
    def __init__(self, api_key, *, base_url=_BASE, timeout=60.0, transport=None):
        self._key, self._base, self._timeout, self._transport = api_key, base_url, timeout, transport

    async def transcribe(self, audio, *, model, filename="voice.ogg", language=None):
        data = {"model": model}
        if language:
            data["language"] = language
        files = {"file": (filename, audio, "application/octet-stream")}
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as c:
            r = await c.post(f"{self._base}/audio/transcriptions",
                             headers={"Authorization": f"Bearer {self._key}"},
                             data=data, files=files)
            r.raise_for_status()
            return r.json()["text"]

    async def speak(self, text, *, model, voice, response_format="wav"):
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as c:
            r = await c.post(f"{self._base}/audio/speech",
                             headers={"Authorization": f"Bearer {self._key}"},
                             json={"model": model, "input": text, "voice": voice,
                                   "response_format": response_format})
            r.raise_for_status()
            return r.content


class GroqAudioSkill:
    def __init__(self, config):
        self._cfg = config

    async def on_inbound(self, raw, ctx):
        att = raw.attachment
        if att is None or att.kind not in ("voice", "audio"):
            return None
        if len(att.data) > _MAX_BYTES:
            ctx.log.warning("groq_audio: audio too large (%d bytes)", len(att.data))
            return None
        try:
            client = GroqClient(ctx.secret("GROQ_API_KEY"))
            text = await client.transcribe(
                att.data, model=self._cfg.get("stt_model", "whisper-large-v3-turbo"),
                filename=att.filename or "voice.ogg")
            return text or None
        except Exception as exc:  # network/HTTP/parse — fall back to a text reply
            ctx.log.warning("groq_audio: transcription failed: %s", exc)
            return None

    async def on_outbound(self, reply, origin, ctx):
        if not origin.came_from_voice or not self._cfg.get("reply_with_voice", True):
            return None
        try:
            client = GroqClient(ctx.secret("GROQ_API_KEY"))
            audio = await client.speak(
                reply.text, model=self._cfg.get("tts_model", "canopylabs/orpheus-v1-english"),
                voice=self._cfg.get("tts_voice", "troy"))
            return [Delivery(kind="voice", data=audio, mime="audio/wav")]
        except Exception as exc:  # degrade to text only
            ctx.log.warning("groq_audio: tts failed: %s", exc)
            return None


def build_skill(config):
    return GroqAudioSkill(config)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/skills/test_groq_audio_skill.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add skills/groq_audio tests/skills/test_groq_audio_skill.py
git commit -m "feat(skills): first-party Groq audio skill (Whisper STT + Orpheus TTS)"
```

---

### Task 6: Gateway voice helper

**Files:**
- Modify: `src/ari/infrastructure/gateway/telegram_adapter.py` (add `voice_to_text`)
- Test: `tests/infrastructure/test_telegram_voice.py`

**Interfaces:**
- Consumes: `SkillManager.run_inbound` (Task 3); `RawInbound`/`Attachment` (Task 1).
- Produces: `async voice_to_text(update, download, manager, is_owner) -> str | None`, where `download: async (media) -> bytes|bytearray`. Returns the transcript, or `None` when there is no voice/audio media or no skill handled it.

- [ ] **Step 1: Write the failing test**

```python
# tests/infrastructure/test_telegram_voice.py
from types import SimpleNamespace

from ari.infrastructure.gateway.telegram_adapter import voice_to_text


class _Manager:
    def __init__(self, reply):
        self.reply = reply
        self.seen = None

    async def run_inbound(self, raw, is_owner):
        self.seen = raw
        return self.reply


def _update_with_voice():
    voice = SimpleNamespace(mime_type="audio/ogg", duration=3)
    msg = SimpleNamespace(voice=voice, audio=None, from_user=SimpleNamespace(id=7), chat_id=42)
    return SimpleNamespace(effective_message=msg, message=msg)


async def test_voice_to_text_downloads_and_transcribes():
    async def download(media):
        return b"OGGBYTES"
    m = _Manager("hola desde audio")
    text = await voice_to_text(_update_with_voice(), download, m, is_owner=True)
    assert text == "hola desde audio"
    assert m.seen.attachment.kind == "voice"
    assert m.seen.attachment.data == b"OGGBYTES"
    assert m.seen.user_id == "7" and m.seen.chat_id == "42"


async def test_voice_to_text_none_when_no_media():
    msg = SimpleNamespace(voice=None, audio=None, from_user=SimpleNamespace(id=7), chat_id=42)
    upd = SimpleNamespace(effective_message=msg, message=msg)
    async def download(media):
        raise AssertionError("should not download")
    assert await voice_to_text(upd, download, _Manager("x"), is_owner=True) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/infrastructure/test_telegram_voice.py -v`
Expected: FAIL — `ImportError: cannot import name 'voice_to_text'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/ari/infrastructure/gateway/telegram_adapter.py` (and add the import at the top):

```python
from ari.domain.skills.models import Attachment, RawInbound


async def voice_to_text(update, download, manager, is_owner: bool) -> str | None:
    """Extract a voice/audio attachment, download its bytes, and transcribe it through
    the skill manager's inbound transforms. Returns the text, or None when there is no
    audio media or no skill handled it. `download` is an async callable(media)->bytes."""
    msg = getattr(update, "effective_message", None) or getattr(update, "message", None)
    if msg is None:
        return None
    media = getattr(msg, "voice", None) or getattr(msg, "audio", None)
    if media is None:
        return None
    data = await download(media)
    raw = RawInbound(
        user_id=str(msg.from_user.id), chat_id=str(msg.chat_id), text=None,
        attachment=Attachment(kind="voice", mime=getattr(media, "mime_type", None) or "audio/ogg",
                              data=bytes(data), duration=getattr(media, "duration", None)))
    return await manager.run_inbound(raw, is_owner)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/infrastructure/test_telegram_voice.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ari/infrastructure/gateway/telegram_adapter.py tests/infrastructure/test_telegram_voice.py
git commit -m "feat(gateway): voice_to_text helper transcribes attachments via skills"
```

---

### Task 7: SkillAdmin & capabilities

**Files:**
- Create: `src/ari/application/skills/skill_admin.py`
- Modify: `src/ari/domain/agent/capabilities.py` (three `Capability` entries)
- Test: `tests/application/test_skill_admin.py`

**Interfaces:**
- Consumes: `SkillManager` (`list`, `set_enabled`) and a `vault_web`-like object exposing `new_link() -> str`.
- Produces: `SkillAdmin(manager, vault_web)` with `list_text() -> str`, `enable(name) -> str`, `disable(name) -> str`. `enable` on a skill still missing secrets returns a message containing the `new_link()` URL.

- [ ] **Step 1: Write the failing tests**

```python
# tests/application/test_skill_admin.py
from ari.application.skills.skill_admin import SkillAdmin
from ari.application.skills.skill_manager import SkillStatus


class _Manager:
    def __init__(self, statuses):
        self._statuses = statuses
        self.enabled_calls = []

    def list(self):
        return self._statuses

    def set_enabled(self, name, enabled):
        self.enabled_calls.append((name, enabled))
        return any(s.name == name for s in self._statuses)


class _VaultWeb:
    def __init__(self):
        self.links = 0

    def new_link(self):
        self.links += 1
        return "https://192.168.0.5:8765/v/tok"


def _status(name, enabled=True, state="active", missing=None):
    return SkillStatus(name, "0.1.0", enabled, state, missing or [], ["inbound_transform"])


def test_list_text_lists_skills():
    admin = SkillAdmin(_Manager([_status("groq_audio")]), _VaultWeb())
    text = admin.list_text()
    assert "groq_audio" in text and "active" in text


def test_list_text_when_empty():
    assert "No hay skills" in SkillAdmin(_Manager([]), _VaultWeb()).list_text()


def test_enable_missing_secret_returns_link():
    vw = _VaultWeb()
    admin = SkillAdmin(_Manager([_status("groq_audio", state="needs_secrets", missing=["GROQ_API_KEY"])]), vw)
    out = admin.enable("groq_audio")
    assert vw.links == 1
    assert "https://192.168.0.5:8765/v/tok" in out
    assert "GROQ_API_KEY" in out


def test_enable_ready_skill_no_link():
    vw = _VaultWeb()
    admin = SkillAdmin(_Manager([_status("groq_audio", state="active")]), vw)
    out = admin.enable("groq_audio")
    assert vw.links == 0 and "Activé" in out


def test_enable_unknown_skill():
    admin = SkillAdmin(_Manager([]), _VaultWeb())
    assert "No existe" in admin.enable("nope")


def test_disable():
    m = _Manager([_status("groq_audio")])
    out = SkillAdmin(m, _VaultWeb()).disable("groq_audio")
    assert ("groq_audio", False) in m.enabled_calls and "Desactivé" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/application/test_skill_admin.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ari.application.skills.skill_admin'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ari/application/skills/skill_admin.py
class SkillAdmin:
    """Owner-facing skill management, callable from the main-process Telegram handlers."""

    def __init__(self, manager, vault_web):
        self._m = manager
        self._vw = vault_web

    def list_text(self) -> str:
        rows = self._m.list()
        if not rows:
            return "No hay skills instalados."
        lines = []
        for s in rows:
            flag = "on" if s.enabled else "off"
            extra = f" — faltan: {', '.join(s.missing_secrets)}" if s.missing_secrets else ""
            lines.append(f"• {s.name} v{s.version} [{flag}/{s.state}]{extra}")
        return "\n".join(lines)

    def enable(self, name: str) -> str:
        if not self._m.set_enabled(name, True):
            return f"No existe el skill '{name}'."
        status = next((s for s in self._m.list() if s.name == name), None)
        if status is not None and status.missing_secrets:
            link = self._vw.new_link()
            return (f"Activé '{name}', pero faltan credenciales: "
                    f"{', '.join(status.missing_secrets)}.\n"
                    f"Cargalas acá (misma red, vence pronto):\n{link}")
        return f"Activé '{name}'."

    def disable(self, name: str) -> str:
        if not self._m.set_enabled(name, False):
            return f"No existe el skill '{name}'."
        return f"Desactivé '{name}'."
```

Add three entries to the `CAPABILITIES` list in `src/ari/domain/agent/capabilities.py`:

```python
    Capability(
        command="skills", menu="Ver skills", owner_only=True,
        summary="Lista los skills instalados y su estado (activo, falta credencial, etc.).",
        usage="/skills"),
    Capability(
        command="skill_on", menu="Activar skill", owner_only=True,
        summary="Activa un skill; si le falta una credencial, Ari te manda un link seguro para cargarla.",
        usage="/skill_on <nombre>"),
    Capability(
        command="skill_off", menu="Desactivar skill", owner_only=True,
        summary="Desactiva un skill sin reiniciar a Ari.",
        usage="/skill_off <nombre>"),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/application/test_skill_admin.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add src/ari/application/skills/skill_admin.py tests/application/test_skill_admin.py src/ari/domain/agent/capabilities.py
git commit -m "feat(skills): SkillAdmin + /skills /skill_on /skill_off capabilities"
```

---

### Task 8: Composition & command wiring (integration)

**Files:**
- Modify: `src/ari/main.py` (construct `SkillManager`; `Components.skills`; `bot_data["skills"]` + `bot_data["skill_admin"]`; pass `extra_names`; voice I/O in `_on_message`/`_dispatch`; register `/skills`, `/skill_on`, `/skill_off`)
- Test: none new (this task is glue over already-tested units); proof is the full suite + an import smoke + a manual run.

**Interfaces:**
- Consumes: `SkillManager` (Task 3), `SkillAdmin` (Task 7), `voice_to_text` (Task 6), `VaultWebMaintainer(extra_names=...)` (Task 4), `InboundContext`/`OutgoingMessage`.
- Produces: no new public API — the running behavior.

This task has no unit test because it wires closures over `app.bot`/`app.bot_data`; every branch it uses is already unit-tested. Follow the edits exactly.

- [ ] **Step 1: Construct the manager in `build()` and add it to `Components`**

Add the field to the `Components` dataclass:

```python
    skills: "SkillManager"
```

At the top of `main.py` add the imports:

```python
from ari.application.skills.skill_manager import SkillManager
from ari.application.skills.skill_admin import SkillAdmin
from ari.domain.skills.models import InboundContext
from ari.infrastructure.gateway.telegram_adapter import voice_to_text
```

In `build()`, construct the manager **before** `VaultWebMaintainer`, then pass its names in:

```python
    skills = SkillManager(settings.skills_dir, vault)
    vault_web = VaultWebMaintainer(
        vault, settings.mcp_config, cert_dir=cert_dir, port=settings.vault_web_port,
        bind=settings.vault_web_bind, ttl_minutes=settings.vault_web_ttl_minutes,
        extra_names=skills.required_secret_names)
```

Add `skills=skills` to the `Components(...)` return.

- [ ] **Step 2: Wire `bot_data` and the SkillAdmin in `_post_init`**

After the existing `app.bot_data["vault_web"] = c.vault_web` line:

```python
    app.bot_data["skills"] = c.skills
    app.bot_data["skill_admin"] = SkillAdmin(c.skills, c.vault_web)
```

- [ ] **Step 3: Handle inbound voice in `_on_message`**

Replace the early `return` in `_on_message` with a voice branch:

```python
async def _on_message(update, _context) -> None:
    inc = TelegramAdapter.to_incoming(update)
    if inc is not None:
        await _dispatch(update, inc.text)
        return
    msg = update.effective_message
    if msg is None or msg.from_user is None:
        return
    if not (getattr(msg, "voice", None) or getattr(msg, "audio", None)):
        return
    if not await _admit(msg):
        return
    is_owner = app.bot_data["gate"].is_owner(str(msg.from_user.id))

    async def _download(media):
        f = await media.get_file()
        return await f.download_as_bytearray()

    text = await voice_to_text(update, _download, app.bot_data["skills"], is_owner)
    if not text:
        await msg.reply_text("No pude procesar ese audio (¿muy largo o error de transcripción?). ¿Lo escribís?")
        return
    await _dispatch(update, text, from_voice=True)
```

- [ ] **Step 4: Speak the reply in `_dispatch` when the turn came from voice**

Change the `_dispatch` signature to accept `from_voice`:

```python
async def _dispatch(update, text: str, from_voice: bool = False) -> None:
```

At the very end of `_dispatch`, where it sends the `route_message` reply, after the existing send loop:

```python
    if reply is not None:
        for part in TelegramAdapter.split_text(reply):
            await msg.reply_text(part)
        if from_voice:
            origin = InboundContext(came_from_voice=True, chat_id=str(msg.chat_id), user_id=user_id)
            is_owner = app.bot_data["gate"].is_owner(user_id)
            deliveries = await app.bot_data["skills"].run_outbound(
                OutgoingMessage(chat_id=str(msg.chat_id), text=reply), origin, is_owner)
            for d in deliveries:
                if d.kind == "voice" and d.data:
                    try:
                        await app.bot.send_voice(chat_id=int(msg.chat_id), voice=bytes(d.data))
                    except Exception:
                        try:
                            await app.bot.send_audio(chat_id=int(msg.chat_id), audio=bytes(d.data))
                        except Exception as exc:
                            logging.warning("voice reply failed for %s: %s", msg.chat_id, exc)
```

(`OutgoingMessage` is already imported in `main.py`; if not, add it from `ari.domain.ports.gateway_port`.)

- [ ] **Step 5: Register the slash commands**

Next to the existing `app.add_handler(CommandHandler("vault", _on_vault))`:

```python
    async def _on_skills(update, _context) -> None:
        msg = update.effective_message
        if msg is None or msg.from_user is None or not await _admit(msg):
            return
        if not app.bot_data["gate"].is_owner(str(msg.from_user.id)):
            await msg.reply_text("Ese comando es solo para el dueño.")
            return
        await _reply_parts(msg, app.bot_data["skill_admin"].list_text())

    async def _on_skill_on(update, context) -> None:
        msg = update.effective_message
        if msg is None or msg.from_user is None or not await _admit(msg):
            return
        if not app.bot_data["gate"].is_owner(str(msg.from_user.id)):
            await msg.reply_text("Ese comando es solo para el dueño.")
            return
        name = " ".join(context.args).strip()
        if not name:
            await msg.reply_text("Uso: /skill_on <nombre>")
            return
        await _reply_parts(msg, app.bot_data["skill_admin"].enable(name))

    async def _on_skill_off(update, context) -> None:
        msg = update.effective_message
        if msg is None or msg.from_user is None or not await _admit(msg):
            return
        if not app.bot_data["gate"].is_owner(str(msg.from_user.id)):
            await msg.reply_text("Ese comando es solo para el dueño.")
            return
        name = " ".join(context.args).strip()
        if not name:
            await msg.reply_text("Uso: /skill_off <nombre>")
            return
        await _reply_parts(msg, app.bot_data["skill_admin"].disable(name))

    app.add_handler(CommandHandler("skills", _on_skills))
    app.add_handler(CommandHandler("skill_on", _on_skill_on))
    app.add_handler(CommandHandler("skill_off", _on_skill_off))
```

- [ ] **Step 6: Verify the whole suite and an import smoke**

Run: `python3 -m pytest -q`
Expected: PASS (no regressions).

Run: `python3 -c "import ari.main"`
Expected: no error (composition imports resolve).

- [ ] **Step 7: Manual smoke (documented, not automated)**

1. `python3 -m ari` (or the project's run command) with a valid `.env`.
2. `/skill_on groq_audio` → Ari replies with the `vault_web` HTTPS link.
3. Open the link on the same LAN, load `GROQ_API_KEY`.
4. Send a Telegram voice note → Ari replies with text **and** a voice message.
5. `/skills` shows `groq_audio [on/active]`; `/skill_off groq_audio` stops it.

- [ ] **Step 8: Commit**

```bash
git add src/ari/main.py
git commit -m "feat(skills): wire SkillManager, voice I/O, and /skills commands into the bot"
```

---

## Notes for the implementer

- The MCP tool subprocess (`src/ari/mcp_server/`) is intentionally **not touched** — skill management is main-process-only in v1 (spec §10). Do not add skill tools there.
- `skills/groq_audio/skill.json` ships with `"enabled": false`. Nothing runs until the owner does `/skill_on groq_audio` and loads the key. This keeps the default install inert and safe.
- Telegram voice notes arrive as OGG/Opus; Groq accepts `ogg`, so no transcoding on the way in. On the way out, `send_voice` may reject WAV — the code falls back to `send_audio`. OGG/Opus transcoding of TTS output is out of scope (spec §16).
- Keep `python3 -m` in front of every pytest/pip invocation.
