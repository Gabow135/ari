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
