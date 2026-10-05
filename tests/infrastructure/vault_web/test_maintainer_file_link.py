import pytest

from ari.infrastructure.vault_web.maintainer import VaultWebMaintainer


def _maintainer(tmp_path, denied):
    return VaultWebMaintainer(
        vault=type("V", (), {"names": lambda self: []})(),
        servers_json=str(tmp_path / "servers.json"),
        cert_dir=str(tmp_path), port=0, bind="127.0.0.1",
        ttl_minutes=10, clock=lambda: 0.0, denied_roots=denied)


def test_new_file_link_returns_f_url_and_keeps_server_up(tmp_path):
    f = tmp_path / "a.pdf"; f.write_text("x")
    m = _maintainer(tmp_path, denied=[])
    try:
        url = m.new_file_link(str(f))
        assert "/f/" in url and url.startswith("https://")
        m.sweep_and_maybe_stop()  # a file token is active -> server stays up
        # (no assertion on internals; just that it doesn't raise)
    finally:
        m.stop()


def test_new_file_link_rejects_denied_and_missing(tmp_path):
    env = tmp_path / ".env"; env.write_text("S=1")
    m = _maintainer(tmp_path, denied=[])
    try:
        with pytest.raises(PermissionError):
            m.new_file_link(str(env))  # .env denied by basename
        with pytest.raises(FileNotFoundError):
            m.new_file_link(str(tmp_path / "nope.pdf"))
    finally:
        m.stop()
