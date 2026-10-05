import os

from ari.infrastructure.vault_web.fs_denylist import default_denied_roots, is_denied


def test_denies_configured_roots_and_env_and_allows_others(tmp_path):
    vault = tmp_path / "vault.enc"
    vault.write_text("x")
    ssh = tmp_path / ".ssh"; ssh.mkdir(); (ssh / "id_rsa").write_text("k")
    roots = [os.path.realpath(str(vault)), os.path.realpath(str(ssh))]
    assert is_denied(str(vault), roots) is True
    assert is_denied(str(ssh / "id_rsa"), roots) is True
    ok = tmp_path / "factura.pdf"; ok.write_text("p")
    assert is_denied(str(ok), roots) is False
    env = tmp_path / ".env"; env.write_text("S=1")
    assert is_denied(str(env), roots) is True  # any .env by basename


def test_symlink_to_denied_target_is_denied(tmp_path):
    secret = tmp_path / "secret"; secret.mkdir(); (secret / "k").write_text("k")
    link = tmp_path / "link"
    os.symlink(str(secret / "k"), str(link))
    roots = [os.path.realpath(str(secret))]
    assert is_denied(str(link), roots) is True  # realpath resolves the symlink


def test_default_denied_roots_includes_home_secret_dirs(tmp_path):
    roots = default_denied_roots(str(tmp_path / "vault.enc"), str(tmp_path / ".ari-claude"))
    home = os.path.expanduser("~")
    assert os.path.join(home, ".ssh") in roots
    assert os.path.join(home, "Library", "Keychains") in roots


def test_denies_case_insensitively(tmp_path):
    env = tmp_path / ".ENV"; env.write_text("S=1")
    assert is_denied(str(env), []) is True
    ssh = tmp_path / ".ssh"; ssh.mkdir()
    roots = [os.path.realpath(str(ssh))]
    assert is_denied(str(tmp_path / ".SSH" / "id_rsa"), roots) is True


def test_normalizes_dotdot_before_check(tmp_path):
    secret = tmp_path / "secret"; secret.mkdir(); (secret / "k").write_text("k")
    roots = [os.path.realpath(str(secret))]
    weird = str(tmp_path / "sub" / ".." / "secret" / "k")
    assert is_denied(weird, roots) is True
