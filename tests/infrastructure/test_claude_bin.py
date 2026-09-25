import os

import pytest

from ari.infrastructure import claude_bin
from ari.infrastructure.claude_bin import resolve_claude_bin


def test_unknown_binary_is_returned_unchanged(monkeypatch):
    monkeypatch.setattr(claude_bin.shutil, "which", lambda _n: None)
    assert resolve_claude_bin("claude") == "claude"


def test_found_binary_returns_full_path(monkeypatch, tmp_path):
    exe = str(tmp_path / "claude")
    monkeypatch.setattr(claude_bin.shutil, "which", lambda _n: exe)
    assert resolve_claude_bin("claude") == exe


@pytest.mark.skipif(os.name != "nt", reason="npm .cmd shims are Windows-only")
def test_npm_cmd_shim_resolves_to_real_exe(monkeypatch, tmp_path):
    shim = tmp_path / "claude.cmd"
    shim.write_text("@echo off")
    exe = tmp_path / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    monkeypatch.setattr(claude_bin.shutil, "which", lambda _n: str(shim))
    assert resolve_claude_bin("claude") == str(exe)


@pytest.mark.skipif(os.name != "nt", reason="npm .cmd shims are Windows-only")
def test_cmd_shim_without_exe_falls_back_to_shim(monkeypatch, tmp_path):
    shim = tmp_path / "claude.cmd"
    shim.write_text("@echo off")
    monkeypatch.setattr(claude_bin.shutil, "which", lambda _n: str(shim))
    assert resolve_claude_bin("claude") == str(shim)
