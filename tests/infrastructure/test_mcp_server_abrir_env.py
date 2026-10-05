# tests/infrastructure/test_mcp_server_abrir_env.py
# The MCP __main__ must build AriTools with a file queue + denylist from env,
# so abrir_archivo enqueues instead of returning the "cola no disponible" message.
import os

from ari.infrastructure.vault_web.fs_denylist import default_denied_roots, is_denied


def test_default_denied_roots_from_env_values(tmp_path):
    vault = str(tmp_path / "vault.enc")
    cfg = str(tmp_path / ".ari-claude")
    roots = default_denied_roots(vault, cfg)
    assert os.path.realpath(vault) in roots
    # a .env anywhere is denied regardless of roots
    env = tmp_path / ".env"; env.write_text("x")
    assert is_denied(str(env), roots) is True
