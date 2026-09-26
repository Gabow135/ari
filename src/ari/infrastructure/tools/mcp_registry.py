"""Loads mcp/servers.json, resolves ${VAR} secrets and writes one resolved MCP
config per role for the Claude CLI (--mcp-config). Hot-reloads on change."""
import json
import logging
import os
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass

from dotenv import dotenv_values

from ari.domain.tools.fs_guard import unsafe_fs_root

log = logging.getLogger("ari.mcp")

OWNER, USERS = "owner", "users"
_ARI_FIELDS = ("access", "description", "guarded")
_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class ServerStatus:
    name: str
    description: str
    access: str
    ok: bool
    detail: str  # "" when ok, else why it is disabled (Spanish, user-facing)


class _Missing(Exception):
    def __init__(self, var: str):
        super().__init__(var)
        self.var = var


def _stamp(path: str) -> tuple[int, int] | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


class McpRegistry:
    def __init__(self, config_path: str, env_file: str, out_dir: str, *,
                 environ: Mapping[str, str] | None = None, platform: str = os.name,
                 which=shutil.which, vault=None, sensitive_paths=()):
        self._config_path, self._env_file, self._out_dir = config_path, env_file, out_dir
        self._environ = environ if environ is not None else os.environ
        self._platform, self._which = platform, which
        self._stamps: tuple | None = None
        self._raw: dict | None = None  # last valid servers.json content
        self._resolved: dict[str, dict] = {}  # name -> CLI-ready server config
        self._meta: dict[str, tuple[str, str]] = {}  # name -> (access, description)
        self._status: list[ServerStatus] = []
        self._paths: dict[bool, str | None] = {True: None, False: None}
        self._write_ok: dict[bool, bool] = {True: True, False: True}
        self._vault = vault
        self._sensitive = tuple(sensitive_paths)
        self._vault_path = getattr(vault, "path", None)

    # ---- public API -------------------------------------------------------

    def servers_for(self, is_owner: bool) -> tuple[tuple[str, ...], str | None]:
        self._refresh()
        return self._names(is_owner), self._paths[is_owner]

    def descriptions(self, is_owner: bool) -> list[tuple[str, str]]:
        self._refresh()
        return [(n, self._meta[n][1]) for n in self._names(is_owner)]

    def status(self) -> list[ServerStatus]:
        self._refresh()
        return list(self._status)

    # ---- internals --------------------------------------------------------

    def _candidate_names(self, is_owner: bool) -> tuple[str, ...]:
        return tuple(n for n in self._resolved
                     if is_owner or self._meta[n][0] == USERS)

    def _names(self, is_owner: bool) -> tuple[str, ...]:
        if not self._write_ok[is_owner]:
            return ()
        return self._candidate_names(is_owner)

    def _refresh(self) -> None:
        stamps = (_stamp(self._config_path), _stamp(self._env_file),
                  _stamp(self._vault_path) if self._vault_path else None)
        if stamps == self._stamps:
            return
        if stamps[0] is None:
            self._raw = None
        else:
            try:
                with open(self._config_path, encoding="utf-8") as f:
                    raw = json.load(f)
                if not isinstance(raw, dict) or not isinstance(raw.get("mcpServers"), dict):
                    raise ValueError("falta el objeto 'mcpServers'")
                self._raw = raw
            except (OSError, ValueError) as exc:  # JSONDecodeError is a ValueError
                log.error("invalid %s (%s); keeping the last valid config",
                          self._config_path, exc)
        # Only advance the stamps once the whole rebuild (including writing the
        # per-role files) succeeded; otherwise the next call retries from
        # scratch instead of getting stuck on a transient write failure.
        if self._rebuild():
            self._stamps = stamps

    def _lookup(self, name: str) -> str | None:
        if self._vault is not None:
            vaulted = self._vault.get(name)
            if vaulted:
                return vaulted
        value = self._environ.get(name)
        if value:
            return value
        if _stamp(self._env_file) is not None:
            file_value = dotenv_values(self._env_file).get(name)
            if file_value:
                return file_value
        return None

    def _subst(self, value):
        if isinstance(value, str):
            def repl(m):
                found = self._lookup(m.group(1))
                if found is None:
                    raise _Missing(m.group(1))
                return found
            return _VAR.sub(repl, value)
        if isinstance(value, list):
            return [self._subst(v) for v in value]
        if isinstance(value, dict):
            return {k: self._subst(v) for k, v in value.items()}
        return value

    def _launcher(self, server: dict) -> dict:
        command = server.get("command")
        if not command:
            return server  # HTTP/url server
        found = self._which(command)
        if found is None:
            raise FileNotFoundError(command)
        if self._platform == "nt" and found.lower().endswith((".cmd", ".bat")):
            # CreateProcess can't run .cmd shims directly (npx/uvx on Windows).
            return {**server, "command": "cmd",
                    "args": ["/c", command, *server.get("args", [])]}
        return server

    def _exposed_in_cli(self, value) -> str | None:
        """Recursively walk all string leaves of the resolved CLI dict and return
        the first sensitive path that any string leaf would expose, or None."""
        if isinstance(value, str):
            return unsafe_fs_root(value, self._sensitive)
        if isinstance(value, list):
            for item in value:
                found = self._exposed_in_cli(item)
                if found is not None:
                    return found
        if isinstance(value, dict):
            for v in value.values():
                found = self._exposed_in_cli(v)
                if found is not None:
                    return found
        return None

    def _rebuild(self) -> bool:
        self._resolved, self._meta, self._status = {}, {}, []
        servers = (self._raw or {}).get("mcpServers", {})
        for name, spec in servers.items():
            spec = spec if isinstance(spec, dict) else {}
            access = spec.get("access", OWNER)
            description = str(spec.get("description", ""))
            detail = ""
            if not _NAME.match(name) or "__" in name:
                detail = f"nombre inválido: {name!r}"
            elif access not in (OWNER, USERS):
                detail = f"access inválido: {access!r}"
            else:
                try:
                    # Ari-only fields are stripped; every other key (command, args,
                    # env, url, headers, type…) goes to the CLI with ${VAR} resolved.
                    cli = {k: self._subst(v) for k, v in spec.items()
                           if k not in _ARI_FIELDS}
                    if spec.get("guarded"):
                        exposed = self._exposed_in_cli(cli)
                        if exposed is not None:
                            detail = f"root inseguro: expone {exposed}"
                    if not detail:
                        cli = self._launcher(cli)
                except _Missing as exc:
                    detail = f"falta {exc.var}"
                except FileNotFoundError:
                    # Show the RAW (unresolved) command, never a resolved ${VAR}
                    # value, so a secret substituted into it never leaks here.
                    detail = f"no se encontró '{spec.get('command')}' en el PATH"
            if detail:
                log.warning("MCP server %r disabled: %s", name, detail)
            else:
                self._resolved[name] = cli
                self._meta[name] = (access, description)
            self._status.append(ServerStatus(name, description, str(access),
                                             not detail, detail))
        ok = True
        for is_owner, file_name in ((True, "owner.json"), (False, "users.json")):
            path, wrote_ok = self._write(file_name, self._candidate_names(is_owner))
            self._paths[is_owner] = path
            self._write_ok[is_owner] = wrote_ok
            ok = ok and wrote_ok
        return ok

    def _write(self, file_name: str, names: tuple[str, ...]) -> tuple[str | None, bool]:
        path = os.path.join(self._out_dir, file_name)
        if not names:
            # A role that lost all its servers (revoked access, rotated secret
            # gone missing, server removed) must never leave a stale config
            # with old secrets lying around.
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            except OSError as exc:
                log.error("could not remove stale %s: %s", path, exc)
                return None, False
            return None, True
        tmp = path + ".tmp"
        try:
            os.makedirs(self._out_dir, exist_ok=True)
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump({"mcpServers": {n: self._resolved[n] for n in names}}, f,
                              ensure_ascii=False, indent=2)
                os.replace(tmp, path)
            except BaseException:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
        except OSError as exc:
            log.error("could not write %s: %s", path, exc)
            return None, False
        return path, True
