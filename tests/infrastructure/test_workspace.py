# tests/infrastructure/test_workspace.py
import os
import pytest

from ari.infrastructure.coder.workspace import Workspace


@pytest.fixture
def root(tmp_path):
    (tmp_path / "proj").mkdir()
    (tmp_path / "proj" / "sub").mkdir()
    (tmp_path / "outside").mkdir()
    return tmp_path


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
    os.symlink(str(root / "outside"), str(link))
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
    os.symlink(str(root / "outside"), str(link))
    with pytest.raises(ValueError):
        await ws.create_branch(str(link), "x")
