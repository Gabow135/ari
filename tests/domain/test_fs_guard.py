import os

from ari.domain.tools.fs_guard import unsafe_fs_root


def test_root_equal_to_sensitive_is_unsafe(tmp_path):
    secret = tmp_path / ".env"
    secret.write_text("x")
    assert unsafe_fs_root(str(secret), [str(secret)]) == str(secret)


def test_root_ancestor_of_sensitive_is_unsafe(tmp_path):
    project = tmp_path / "proj"
    (project).mkdir()
    env = project / ".env"
    env.write_text("x")
    # root is the parent that CONTAINS the project's .env
    assert unsafe_fs_root(str(tmp_path), [str(env)]) == str(env)


def test_sibling_root_is_safe(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    vault = tmp_path / ".ari" / "vault.enc"
    vault.parent.mkdir()
    vault.write_text("x")
    # work does NOT contain the vault → safe
    assert unsafe_fs_root(str(work), [str(vault)]) is None


def test_child_root_not_flagged_by_sibling_file(tmp_path):
    # root ~/.ari/work, vault ~/.ari/vault.enc — vault is not under work
    ari = tmp_path / ".ari"
    (ari / "work").mkdir(parents=True)
    vault = ari / "vault.enc"
    vault.write_text("x")
    assert unsafe_fs_root(str(ari / "work"), [str(vault)]) is None


def test_symlink_root_into_project_is_unsafe(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".env").write_text("x")
    link = tmp_path / "link"
    os.symlink(project, link)
    assert unsafe_fs_root(str(link), [str(project / ".env")]) == str(project / ".env")


def test_no_sensitive_paths_is_safe(tmp_path):
    assert unsafe_fs_root(str(tmp_path), []) is None
