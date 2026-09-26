import json

from ari.infrastructure.vault_web.names import configurable_secret_names


def _write(tmp_path, cfg):
    p = tmp_path / "servers.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return str(p)


def test_lists_var_names_minus_fs_root(tmp_path):
    cfg = {"mcpServers": {
        "google": {"env": {"A": "${GOOGLE_OAUTH_CLIENT_ID}", "B": "${GOOGLE_OAUTH_CLIENT_SECRET}"}},
        "mysql": {"env": {"P": "${ARI_MYSQL_PASS}"}},
        "filesystem": {"args": ["${ARI_FS_ROOT}"]},
    }}
    assert configurable_secret_names(_write(tmp_path, cfg)) == [
        "ARI_MYSQL_PASS", "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET"]


def test_missing_file_returns_empty(tmp_path):
    assert configurable_secret_names(str(tmp_path / "nope.json")) == []
