# tests/infrastructure/test_workspace.py
import os
import subprocess

import pytest

from ari.infrastructure.coder.workspace import Workspace


@pytest.fixture
def root(tmp_path):
    (tmp_path / "proj").mkdir()
    (tmp_path / "proj" / "sub").mkdir()
    (tmp_path / "outside").mkdir()
    return tmp_path


def _link_dir(target, link):
    """Symlink a directory; on Windows without symlink privilege, use a junction."""
    try:
        os.symlink(str(target), str(link), target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        import _winapi
        _winapi.CreateJunction(str(target), str(link))


def test_resolve_accepts_inside_root(root):
    ws = Workspace(str(root))
    got = ws.resolve("proj/sub", default_dir=str(root / "proj"))
    assert got == os.path.realpath(str(root / "proj" / "sub"))


def test_resolve_defaults_when_target_none(root):
    ws = Workspace(str(root))
    assert ws.resolve(None, default_dir=str(root / "proj")) == os.path.realpath(str(root / "proj"))


def test_resolve_rejects_parent_traversal(root):
    ws = Workspace(str(root / "proj"))
    with pytest.raises(ValueError):
        ws.resolve("../outside", default_dir=str(root / "proj"))


def test_resolve_rejects_absolute_escape(root):
    ws = Workspace(str(root / "proj"))
    with pytest.raises(ValueError):
        ws.resolve("/etc", default_dir=str(root / "proj"))


def test_resolve_rejects_symlink_escape(root):
    ws = Workspace(str(root / "proj"))
    link = root / "proj" / "escape"
    _link_dir(root / "outside", link)
    with pytest.raises(ValueError):
        ws.resolve("escape", default_dir=str(root / "proj"))


@pytest.mark.asyncio
async def test_create_branch_rejects_outside_root(root):
    ws = Workspace(str(root / "proj"))
    with pytest.raises(ValueError):
        await ws.create_branch("/tmp", "x")


@pytest.mark.asyncio
async def test_create_branch_rejects_symlink_escape(root):
    """A symlink inside the root pointing outside must be rejected before git runs."""
    ws = Workspace(str(root / "proj"))
    link = root / "proj" / "escape_link"
    _link_dir(root / "outside", link)
    with pytest.raises(ValueError):
        await ws.create_branch(str(link), "x")


# ---------------------------------------------------------------------------
# Fixture: real git repository for finalize-helper tests
# ---------------------------------------------------------------------------


@pytest.fixture
def git_repo(tmp_path):
    """Create a minimal real git repo and return (repo_path, default_branch)."""
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(["git", "-C", str(repo)] + list(args), check=True, capture_output=True)

    git("init")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (repo / "file.txt").write_text("initial")
    git("add", "-A")
    git("commit", "-m", "init")

    # Capture the actual default branch name (may be main or master).
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
        check=True, capture_output=True, text=True,
    )
    default_branch = result.stdout.strip()

    return repo, default_branch


# ---------------------------------------------------------------------------
# Tests for current_branch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_current_branch_returns_name(git_repo):
    repo, default_branch = git_repo
    ws = Workspace(str(repo))
    branch = await ws.current_branch(str(repo))
    assert branch == default_branch


@pytest.mark.asyncio
async def test_current_branch_rejects_outside_root(git_repo):
    repo, _default = git_repo
    ws = Workspace(str(repo))
    with pytest.raises(ValueError):
        await ws.current_branch("/tmp")


# ---------------------------------------------------------------------------
# Tests for checkout
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_checkout_switches_branch(git_repo):
    repo, default_branch = git_repo

    # Create a feature branch with an extra file.
    def git(*args):
        subprocess.run(["git", "-C", str(repo)] + list(args), check=True, capture_output=True)

    git("checkout", "-b", "feat")
    (repo / "extra.txt").write_text("extra")
    git("add", "-A")
    git("commit", "-m", "add extra")

    ws = Workspace(str(repo))

    # Switch back to base — extra file should disappear.
    await ws.checkout(str(repo), default_branch)
    assert not (repo / "extra.txt").exists()

    # Switch back to feat — extra file should reappear.
    await ws.checkout(str(repo), "feat")
    assert (repo / "extra.txt").exists()


# ---------------------------------------------------------------------------
# Tests for merge_into
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_merge_into_brings_branch_commit_to_base(git_repo):
    repo, default_branch = git_repo

    def git(*args):
        subprocess.run(["git", "-C", str(repo)] + list(args), check=True, capture_output=True)

    # Create a feature branch, add a new file, commit, go back to base.
    git("checkout", "-b", "ari/tg-x")
    (repo / "feature.txt").write_text("feature content")
    git("add", "-A")
    git("commit", "-m", "feat: add feature.txt")
    git("checkout", default_branch)

    ws = Workspace(str(repo))
    await ws.merge_into(str(repo), default_branch, "ari/tg-x")

    # Repo must be on base and the feature file must now exist there.
    current = await ws.current_branch(str(repo))
    assert current == default_branch
    assert (repo / "feature.txt").exists()


@pytest.mark.asyncio
async def test_merge_into_conflict_raises_and_stays_on_base(git_repo):
    repo, default_branch = git_repo

    def git(*args):
        subprocess.run(["git", "-C", str(repo)] + list(args), check=True, capture_output=True)

    # Create a branch, edit file.txt differently from base, then edit base too.
    git("checkout", "-b", "conflict-branch")
    (repo / "file.txt").write_text("branch version")
    git("add", "-A")
    git("commit", "-m", "branch: edit file.txt")

    git("checkout", default_branch)
    (repo / "file.txt").write_text("base version")
    git("add", "-A")
    git("commit", "-m", "base: edit file.txt differently")

    ws = Workspace(str(repo))
    with pytest.raises(RuntimeError):
        await ws.merge_into(str(repo), default_branch, "conflict-branch")

    # Repo must be back on base with no merge in progress.
    current = await ws.current_branch(str(repo))
    assert current == default_branch
    assert not (repo / ".git" / "MERGE_HEAD").exists()


@pytest.mark.asyncio
async def test_merge_into_rejects_outside_root(git_repo):
    repo, default_branch = git_repo
    ws = Workspace(str(repo))
    with pytest.raises(ValueError):
        await ws.merge_into("/tmp", default_branch, "some-branch")
