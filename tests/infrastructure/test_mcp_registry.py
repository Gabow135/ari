import json
import os

import pytest

from ari.infrastructure.tools.mcp_registry import McpRegistry
from tests.fakes import FakeVault

CONFIG = {
    "mcpServers": {
        "google": {"command": "uvx", "args": ["workspace-mcp"],
                   "env": {"GOOGLE_OAUTH_CLIENT_ID": "${GID}"},
                   "access": "owner", "description": "Gmail del creador"},
        "mysql": {"command": "npx", "args": ["-y", "mysql-mcp", "--db=${DB}"],
                  "env": {"MYSQL_PASS": "${DBPASS}"}, "description": "Base (solo lectura)"},
        "docs": {"url": "https://docs.example/mcp",
                 "headers": {"Authorization": "Bearer ${DOCS_TOKEN}"},
                 "access": "users", "description": "Documentación"},
    }
}
ENV = {"GID": "gid-1", "DB": "ventas", "DBPASS": "s3cret", "DOCS_TOKEN": "tok"}


def _which(name):
    return {"uvx": "/usr/bin/uvx", "npx": "/usr/bin/npx"}.get(name)


def _registry(tmp_path, config=CONFIG, environ=ENV, platform="posix", which=_which,
              env_text="", vault=None):
    cfg = tmp_path / "servers.json"
    cfg.write_text(json.dumps(config), encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text(env_text, encoding="utf-8")
    return McpRegistry(str(cfg), str(env_file), str(tmp_path / "out"),
                       environ=dict(environ), platform=platform, which=which,
                       vault=vault), cfg, env_file


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)["mcpServers"]


def test_owner_gets_all_servers_resolved_and_stripped(tmp_path):
    reg, _, _ = _registry(tmp_path)
    names, path = reg.servers_for(is_owner=True)
    assert set(names) == {"google", "mysql", "docs"}
    servers = _load(path)
    assert servers["google"]["env"]["GOOGLE_OAUTH_CLIENT_ID"] == "gid-1"
    assert "access" not in servers["google"] and "description" not in servers["google"]
    assert os.path.dirname(path) == str(tmp_path / "out")


def test_users_only_get_users_servers(tmp_path):
    reg, _, _ = _registry(tmp_path)
    names, path = reg.servers_for(is_owner=False)
    assert names == ("docs",)
    assert set(_load(path)) == {"docs"}


def test_default_access_is_owner(tmp_path):
    reg, _, _ = _registry(tmp_path)
    assert "mysql" not in reg.servers_for(is_owner=False)[0]  # no "access" key → owner


def test_var_embedded_in_longer_string_is_substituted(tmp_path):
    reg, _, _ = _registry(tmp_path)
    servers = _load(reg.servers_for(is_owner=True)[1])
    assert servers["mysql"]["args"] == ["-y", "mysql-mcp", "--db=ventas"]
    assert servers["docs"]["headers"]["Authorization"] == "Bearer tok"


def test_missing_var_disables_only_that_server(tmp_path):
    env = {k: v for k, v in ENV.items() if k != "DBPASS"}
    reg, _, _ = _registry(tmp_path, environ=env)
    assert "mysql" not in reg.servers_for(is_owner=True)[0]
    status = {s.name: s for s in reg.status()}
    assert not status["mysql"].ok and status["mysql"].detail == "falta DBPASS"
    assert status["google"].ok


def test_env_file_used_when_not_in_os_environ_and_hot_reloaded(tmp_path):
    env = {k: v for k, v in ENV.items() if k != "DBPASS"}
    reg, _, env_file = _registry(tmp_path, environ=env, env_text="DBPASS=from-file\n")
    assert _load(reg.servers_for(True)[1])["mysql"]["env"]["MYSQL_PASS"] == "from-file"
    env_file.write_text("DBPASS=rotated-longer\n", encoding="utf-8")
    assert _load(reg.servers_for(True)[1])["mysql"]["env"]["MYSQL_PASS"] == "rotated-longer"


def test_os_environ_wins_over_env_file(tmp_path):
    reg, _, _ = _registry(tmp_path, env_text="DBPASS=from-file\n")
    assert _load(reg.servers_for(True)[1])["mysql"]["env"]["MYSQL_PASS"] == "s3cret"


def test_missing_launcher_disables_server(tmp_path):
    reg, _, _ = _registry(tmp_path, which=lambda n: "/usr/bin/npx" if n == "npx" else None)
    assert "google" not in reg.servers_for(True)[0]
    status = {s.name: s for s in reg.status()}
    assert status["google"].detail == "no se encontró 'uvx' en el PATH"


def test_windows_cmd_launchers_are_wrapped(tmp_path):
    which = lambda n: {"uvx": r"C:\u\uvx.exe", "npx": r"C:\n\npx.cmd"}.get(n)
    reg, _, _ = _registry(tmp_path, platform="nt", which=which)
    servers = _load(reg.servers_for(True)[1])
    assert servers["mysql"]["command"] == "cmd"
    assert servers["mysql"]["args"] == ["/c", "npx", "-y", "mysql-mcp", "--db=ventas"]
    assert servers["google"]["command"] == "uvx"  # .exe needs no wrapping


def test_invalid_access_disables_server(tmp_path):
    cfg = {"mcpServers": {"x": {"command": "npx", "access": "everyone"}}}
    reg, _, _ = _registry(tmp_path, config=cfg)
    assert reg.servers_for(True) == ((), None)
    assert reg.status()[0].detail == "access inválido: 'everyone'"


def test_invalid_json_keeps_last_valid_config(tmp_path):
    reg, cfg, _ = _registry(tmp_path)
    assert reg.servers_for(True)[0]
    cfg.write_text("{ not json", encoding="utf-8")
    assert set(reg.servers_for(True)[0]) == {"google", "mysql", "docs"}


def test_missing_config_file_means_no_servers(tmp_path):
    reg = McpRegistry(str(tmp_path / "nope.json"), str(tmp_path / ".env"),
                      str(tmp_path / "out"), environ={}, which=_which)
    assert reg.servers_for(True) == ((), None)
    assert reg.status() == []


def test_descriptions(tmp_path):
    reg, _, _ = _registry(tmp_path)
    assert reg.descriptions(is_owner=False) == [("docs", "Documentación")]
    assert ("google", "Gmail del creador") in reg.descriptions(is_owner=True)


# ---- I1: stale role files never linger --------------------------------

def test_stale_role_file_deleted_when_role_loses_all_servers(tmp_path):
    cfg = {"mcpServers": {"users_srv": {"command": "npx", "env": {"S": "${SEC}"},
                                        "access": "users", "description": "d"}}}
    reg, cfg_path, _ = _registry(tmp_path, config=cfg, environ={"SEC": "s3cret"})
    _, users_path = reg.servers_for(is_owner=False)
    assert users_path is not None and os.path.exists(users_path)
    cfg["mcpServers"]["users_srv"]["access"] = "owner"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    assert reg.servers_for(is_owner=False) == ((), None)
    assert not os.path.exists(users_path)


def test_deleting_servers_json_removes_both_role_files(tmp_path):
    reg, cfg_path, _ = _registry(tmp_path)
    _, owner_path = reg.servers_for(is_owner=True)
    _, users_path = reg.servers_for(is_owner=False)
    assert os.path.exists(owner_path) and os.path.exists(users_path)
    cfg_path.unlink()
    assert reg.servers_for(is_owner=True) == ((), None)
    assert not os.path.exists(owner_path)
    assert not os.path.exists(users_path)


# ---- I2: atomic write, restrictive permissions from creation -----------

def test_write_is_atomic_with_no_leftover_temp_files(tmp_path):
    reg, _, _ = _registry(tmp_path)
    _, path = reg.servers_for(is_owner=True)
    content = _load(path)
    assert content["google"]["env"]["GOOGLE_OAUTH_CLIENT_ID"] == "gid-1"
    out_dir = tmp_path / "out"
    leftovers = [p for p in os.listdir(out_dir) if p not in ("owner.json", "users.json")]
    assert leftovers == []
    if os.name != "nt":
        mode = os.stat(path).st_mode & 0o777
        assert mode == 0o600


# ---- M1: valid JSON that isn't an object is treated as invalid --------

@pytest.mark.parametrize("bad", ["[]", "null", '"just a string"'])
def test_valid_json_non_object_keeps_last_valid_config(tmp_path, bad):
    reg, cfg, _ = _registry(tmp_path)
    assert reg.servers_for(True)[0]
    cfg.write_text(bad, encoding="utf-8")
    assert set(reg.servers_for(True)[0]) == {"google", "mysql", "docs"}


# ---- M2: OSError while writing is contained ----------------------------

def test_unwritable_out_dir_falls_back_to_web_only_and_does_not_raise(tmp_path):
    out_path = tmp_path / "out"
    out_path.write_text("not a directory", encoding="utf-8")
    cfg = tmp_path / "servers.json"
    cfg.write_text(json.dumps(CONFIG), encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    reg = McpRegistry(str(cfg), str(env_file), str(out_path),
                      environ=dict(ENV), which=_which)
    assert reg.servers_for(is_owner=True) == ((), None)
    assert reg.servers_for(is_owner=False) == ((), None)


def test_unwritable_out_dir_retries_once_fixed(tmp_path):
    out_path = tmp_path / "out"
    out_path.write_text("not a directory", encoding="utf-8")
    cfg = tmp_path / "servers.json"
    cfg.write_text(json.dumps(CONFIG), encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    reg = McpRegistry(str(cfg), str(env_file), str(out_path),
                      environ=dict(ENV), which=_which)
    assert reg.servers_for(is_owner=True) == ((), None)  # stamps not advanced
    out_path.unlink()
    names, path = reg.servers_for(is_owner=True)
    assert set(names) == {"google", "mysql", "docs"}
    assert path is not None


# ---- M3: server name validation -----------------------------------------

@pytest.mark.parametrize("bad_name", ["bad name", "bad;name", "a__b", "name$"])
def test_invalid_server_name_is_disabled(tmp_path, bad_name):
    cfg = {"mcpServers": {bad_name: {"command": "npx"}}}
    reg, _, _ = _registry(tmp_path, config=cfg)
    assert reg.servers_for(True) == ((), None)
    assert reg.status()[0].detail == f"nombre inválido: {bad_name!r}"


def test_valid_server_names_are_not_flagged(tmp_path):
    cfg = {"mcpServers": {"good-name_1": {"command": "npx"}}}
    reg, _, _ = _registry(tmp_path, config=cfg)
    assert reg.servers_for(True)[0] == ("good-name_1",)


# ---- M6: missing-launcher detail never leaks a resolved secret ---------

def test_missing_launcher_detail_shows_raw_unresolved_command(tmp_path):
    cfg = {"mcpServers": {"x": {"command": "${CMD}"}}}
    reg, _, _ = _registry(tmp_path, config=cfg, environ={"CMD": "totally-fake-cmd"},
                          which=lambda n: None)
    assert reg.servers_for(True) == ((), None)
    assert reg.status()[0].detail == "no se encontró '${CMD}' en el PATH"


# ---- V1: vault resolution ---------------------------------------------------

def test_vault_value_is_used(tmp_path):
    reg, _, _ = _registry(tmp_path, environ={}, vault=FakeVault(dict(ENV)))
    servers = _load(reg.servers_for(is_owner=True)[1])
    assert servers["google"]["env"]["GOOGLE_OAUTH_CLIENT_ID"] == "gid-1"


def test_vault_wins_over_env_and_dotenv(tmp_path):
    reg, _, _ = _registry(tmp_path, env_text="GID=from-file\n",
                          vault=FakeVault({"GID": "from-vault", "DB": "ventas",
                                           "DBPASS": "s3cret", "DOCS_TOKEN": "tok"}))
    servers = _load(reg.servers_for(is_owner=True)[1])
    assert servers["google"]["env"]["GOOGLE_OAUTH_CLIENT_ID"] == "from-vault"


def test_env_used_when_absent_from_vault(tmp_path):
    reg, _, _ = _registry(tmp_path, vault=FakeVault({}))  # empty vault
    servers = _load(reg.servers_for(is_owner=True)[1])
    assert servers["google"]["env"]["GOOGLE_OAUTH_CLIENT_ID"] == "gid-1"  # from ENV


def test_missing_everywhere_disables_server(tmp_path):
    env = {k: v for k, v in ENV.items() if k != "DBPASS"}
    reg, _, _ = _registry(tmp_path, environ=env, vault=FakeVault({}))
    status = {s.name: s for s in reg.status()}
    assert not status["mysql"].ok and status["mysql"].detail == "falta DBPASS"


def test_guarded_server_disabled_when_root_exposes_sensitive(tmp_path):
    cfg = {"mcpServers": {"filesystem": {
        "command": "npx",
        "args": ["-y", "server-filesystem", "${ARI_FS_ROOT}"],
        "guarded": True, "access": "owner", "description": "Archivos"}}}
    # root == the sensitive .env's parent (contains it)
    root = str(tmp_path)
    sensitive = (str(tmp_path / ".env"),)
    cfgp = tmp_path / "servers.json"
    cfgp.write_text(json.dumps(cfg), encoding="utf-8")
    envp = tmp_path / ".env"
    envp.write_text("", encoding="utf-8")
    reg = McpRegistry(str(cfgp), str(envp), str(tmp_path / "out"),
                      environ={"ARI_FS_ROOT": root}, which=_which,
                      sensitive_paths=sensitive)
    assert reg.servers_for(True) == ((), None)
    assert reg.status()[0].detail == f"root inseguro: expone {tmp_path / '.env'}"


def test_guarded_server_enabled_for_safe_root(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    cfg = {"mcpServers": {"filesystem": {
        "command": "npx",
        "args": ["-y", "server-filesystem", "${ARI_FS_ROOT}"],
        "guarded": True, "access": "owner", "description": "Archivos"}}}
    cfgp = tmp_path / "servers.json"
    cfgp.write_text(json.dumps(cfg), encoding="utf-8")
    envp = tmp_path / ".env"
    envp.write_text("", encoding="utf-8")
    reg = McpRegistry(str(cfgp), str(envp), str(tmp_path / "out"),
                      environ={"ARI_FS_ROOT": str(work)}, which=_which,
                      sensitive_paths=(str(tmp_path / ".ari" / "vault.enc"),))
    names, path = reg.servers_for(True)
    assert names == ("filesystem",)
    servers = _load(path)
    assert "guarded" not in servers["filesystem"]  # Ari-only field stripped


def test_guarded_server_disabled_when_root_exposed_via_env_value(tmp_path):
    """A guarded server whose sensitive path appears in an env dict value is disabled."""
    cfg = {"mcpServers": {"filesystem": {
        "command": "npx",
        "args": ["-y", "server-filesystem", "/safe/work"],
        "env": {"FS_ROOT": "${ARI_FS_ROOT}"},
        "guarded": True, "access": "owner", "description": "Archivos"}}}
    root = str(tmp_path)  # root via env value, not args
    sensitive = (str(tmp_path / ".env"),)
    cfgp = tmp_path / "servers.json"
    cfgp.write_text(json.dumps(cfg), encoding="utf-8")
    envp = tmp_path / ".env"
    envp.write_text("", encoding="utf-8")
    reg = McpRegistry(str(cfgp), str(envp), str(tmp_path / "out"),
                      environ={"ARI_FS_ROOT": root}, which=_which,
                      sensitive_paths=sensitive)
    assert reg.servers_for(True) == ((), None)
    assert reg.status()[0].detail == f"root inseguro: expone {tmp_path / '.env'}"


def test_guarded_server_disabled_when_root_exposed_via_command(tmp_path):
    """A guarded server whose command string is itself a sensitive path is disabled."""
    sensitive_cmd = str(tmp_path / "secret-bin")
    # Make the command 'found' so _launcher doesn't error first
    cfg = {"mcpServers": {"guarded-cmd": {
        "command": "${SECRET_CMD}",
        "args": [],
        "guarded": True, "access": "owner", "description": "test"}}}
    cfgp = tmp_path / "servers.json"
    cfgp.write_text(json.dumps(cfg), encoding="utf-8")
    envp = tmp_path / ".env"
    envp.write_text("", encoding="utf-8")
    # Sensitive path is the command itself; the command IS the sensitive path parent
    sensitive = (str(tmp_path / "secret-bin" / "key"),)
    reg = McpRegistry(str(cfgp), str(envp), str(tmp_path / "out"),
                      environ={"SECRET_CMD": sensitive_cmd},
                      which=lambda n: n,  # always found
                      sensitive_paths=sensitive)
    assert reg.servers_for(True) == ((), None)
    assert reg.status()[0].detail == f"root inseguro: expone {tmp_path / 'secret-bin' / 'key'}"


def test_guarded_server_not_offered_to_users(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    cfg = {"mcpServers": {"filesystem": {
        "command": "npx", "args": ["-y", "sf", "${ARI_FS_ROOT}"],
        "guarded": True, "access": "owner", "description": "Archivos"}}}
    cfgp = tmp_path / "servers.json"
    cfgp.write_text(json.dumps(cfg), encoding="utf-8")
    envp = tmp_path / ".env"
    envp.write_text("", encoding="utf-8")
    reg = McpRegistry(str(cfgp), str(envp), str(tmp_path / "out"),
                      environ={"ARI_FS_ROOT": str(work)}, which=_which,
                      sensitive_paths=())
    assert reg.servers_for(is_owner=False) == ((), None)
