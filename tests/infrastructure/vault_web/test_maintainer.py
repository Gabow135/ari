import re

from ari.infrastructure.vault_web import maintainer as mod
from ari.infrastructure.vault_web.maintainer import VaultWebMaintainer
from tests.fakes import FakeVault


def _make(tmp_path, monkeypatch, clock):
    monkeypatch.setattr(mod, "detect_lan_ip", lambda: "127.0.0.1")
    (tmp_path / "servers.json").write_text(
        '{"mcpServers": {"mysql": {"env": {"P": "${ARI_MYSQL_PASS}"}}}}', encoding="utf-8")
    return VaultWebMaintainer(
        FakeVault(), str(tmp_path / "servers.json"), cert_dir=str(tmp_path / "certs"),
        port=0, bind="127.0.0.1", ttl_minutes=10, clock=clock)


def test_new_link_url_shape_and_server_up(tmp_path, monkeypatch):
    m = _make(tmp_path, monkeypatch, clock=lambda: 0.0)
    try:
        url = m.new_link()
        assert re.match(r"^https://127\.0\.0\.1:\d+/v/[\w-]+$", url)
        assert m._server is not None            # server started
    finally:
        m.stop()


def test_reaper_stops_server_when_tokens_expire(tmp_path, monkeypatch):
    now = [0.0]
    m = _make(tmp_path, monkeypatch, clock=lambda: now[0])
    m.new_link()
    assert m._server is not None
    now[0] += 601                                # past the 10-min TTL
    m.sweep_and_maybe_stop()
    assert m._server is None                     # idle → stopped
