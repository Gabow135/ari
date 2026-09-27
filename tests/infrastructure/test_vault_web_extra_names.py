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


def test_writable_names_never_includes_ari_fs_root(tmp_path):
    """ARI_FS_ROOT must be excluded from the writable allowlist even when a skill
    declares it in required_secrets and servers.json references it as a variable."""
    sj = tmp_path / "servers.json"
    sj.write_text(
        '{"mcpServers": {"fs": {"env": {"ROOT": "${ARI_FS_ROOT}", "KEY": "${GROQ_API_KEY}"}}}}'
    )
    m = VaultWebMaintainer(
        FakeVault(), str(sj), cert_dir=str(tmp_path), port=0, bind="127.0.0.1",
        ttl_minutes=10,
        extra_names=lambda: ["ARI_FS_ROOT", "GROQ_API_KEY"],
    )
    names = m._writable_names()
    assert "ARI_FS_ROOT" not in names
    assert "GROQ_API_KEY" in names
