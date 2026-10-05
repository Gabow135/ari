import os

import pytest

from ari.infrastructure.workspace.user_workspace import UserWorkspace, Workspaces


def test_write_read_list_delete_roundtrip(tmp_path):
    ws = UserWorkspace(str(tmp_path), "42")
    rel = ws.write_text("notas/plan.txt", "hola")
    assert rel == os.path.join("notas", "plan.txt")
    assert ws.read_text("notas/plan.txt") == "hola"
    assert ws.list("notas") == ["plan.txt"]
    ws.delete("notas/plan.txt")
    with pytest.raises(FileNotFoundError):
        ws.read_text("notas/plan.txt")


def test_rejects_parent_traversal_and_absolute(tmp_path):
    ws = UserWorkspace(str(tmp_path), "42")
    with pytest.raises(ValueError):
        ws.resolve("../escape.txt")
    with pytest.raises(ValueError):
        ws.resolve("/etc/passwd")


def test_rejects_symlink_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    ws = UserWorkspace(str(tmp_path), "42")
    ws.ensure()
    os.symlink(str(outside), os.path.join(ws.root, "link"), target_is_directory=True)
    with pytest.raises(ValueError):
        ws.resolve("link/secret.txt")


def test_file_size_cap(tmp_path):
    ws = UserWorkspace(str(tmp_path), "42", max_file_bytes=4)
    with pytest.raises(ValueError):
        ws.write_text("big.txt", "toolong")


def test_user_ids_are_sanitized_and_isolated(tmp_path):
    a = Workspaces(str(tmp_path)).for_user("../../etc")
    b = Workspaces(str(tmp_path)).for_user("99")
    a.ensure(); b.ensure()
    base = os.path.realpath(str(tmp_path))
    assert a.root.startswith(base + os.sep)
    assert os.path.dirname(a.root) == base  # one segment only, no traversal
    assert a.root != b.root


# ---------------------------------------------------------------------------
# read_bytes
# ---------------------------------------------------------------------------

def test_read_bytes_roundtrip(tmp_path):
    ws = UserWorkspace(str(tmp_path), "42")
    ws.write_text("docs/report.txt", "hello bytes")
    data = ws.read_bytes("docs/report.txt")
    assert data == b"hello bytes"


def test_read_bytes_rejects_traversal(tmp_path):
    ws = UserWorkspace(str(tmp_path), "42")
    with pytest.raises(ValueError):
        ws.read_bytes("../escape.bin")


def test_read_bytes_missing_file_raises(tmp_path):
    ws = UserWorkspace(str(tmp_path), "42")
    with pytest.raises(FileNotFoundError):
        ws.read_bytes("nonexistent.pdf")


def test_read_bytes_size_cap(tmp_path):
    ws = UserWorkspace(str(tmp_path), "42")
    ws.write_text("big.txt", "A" * 100)
    with pytest.raises(ValueError, match="too large"):
        ws.read_bytes("big.txt", max_bytes=10)


def test_write_bytes_roundtrip(tmp_path):
    from ari.infrastructure.workspace.user_workspace import UserWorkspace
    ws = UserWorkspace(str(tmp_path), "u1")
    rel = ws.write_bytes("correos/adjuntos/a/factura.pdf", b"%PDF-1.4 bytes")
    assert rel == "correos/adjuntos/a/factura.pdf"
    assert ws.read_bytes(rel) == b"%PDF-1.4 bytes"


def test_write_bytes_rejects_oversize(tmp_path):
    import pytest
    from ari.infrastructure.workspace.user_workspace import UserWorkspace
    ws = UserWorkspace(str(tmp_path), "u1")
    with pytest.raises(ValueError):
        ws.write_bytes("big.bin", b"x" * 11, max_bytes=10)


def test_write_bytes_refuses_escape(tmp_path):
    import pytest
    from ari.infrastructure.workspace.user_workspace import UserWorkspace
    ws = UserWorkspace(str(tmp_path), "u1")
    with pytest.raises(ValueError):
        ws.write_bytes("../escape.bin", b"x")
