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
