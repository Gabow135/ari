import os
import subprocess
import tempfile

import pytest

from ari.infrastructure.coder.claude_code_coder import ClaudeCodeCoder
from ari.domain.coding.entities import CodingInstruction, CodingPlan


# ---------------------------------------------------------------------------
# Unit tests — injected runners, no CLI
# ---------------------------------------------------------------------------

async def test_plan_uses_runner_and_returns_summary():
    async def plan_runner(instr, target, model):
        assert model == "claude-sonnet-4-6"
        return '{"result": "1. add endpoint\\n2. test it"}'

    coder = ClaudeCodeCoder(plan_runner=plan_runner)
    plan = await coder.plan(CodingInstruction("u1", "add healthcheck"), "/repo")
    assert "add endpoint" in plan.summary
    assert plan.target_dir == "/repo"


async def test_execute_reports_error_on_failed_runner():
    async def exec_runner(instr, target, model):
        return '{"result": "nope", "is_error": true}'

    coder = ClaudeCodeCoder(exec_runner=exec_runner)
    plan = CodingPlan("s", "/repo", "add healthcheck")
    res = await coder.execute(plan, "ari/tg-1")
    assert res.ok is False and res.branch == "ari/tg-1"


async def test_plan_preserves_instruction_text():
    async def plan_runner(instr, target, model):
        return '{"result": "step 1"}'

    coder = ClaudeCodeCoder(plan_runner=plan_runner)
    plan = await coder.plan(CodingInstruction("u1", "fix the bug"), "/repo")
    assert plan.instruction_text == "fix the bug"


async def test_execute_success_returns_ok():
    commits_called = []

    async def exec_runner(instr, target, model):
        return '{"result": "done", "is_error": false}'

    async def fake_collect(target_dir):
        commits_called.append(target_dir)
        return ["file.py"], ["abc1234"], None  # (files, commits, error_detail)

    coder = ClaudeCodeCoder(exec_runner=exec_runner)
    coder._collect_git = fake_collect  # patch for unit test

    plan = CodingPlan("s", "/repo", "add feature")
    res = await coder.execute(plan, "ari/tg-2")
    assert res.ok is True
    assert res.branch == "ari/tg-2"


async def test_execute_runner_exception_returns_not_ok():
    async def exec_runner(instr, target, model):
        raise RuntimeError("subprocess failed")

    coder = ClaudeCodeCoder(exec_runner=exec_runner)
    plan = CodingPlan("s", "/repo", "do work")
    res = await coder.execute(plan, "ari/tg-3")
    assert res.ok is False
    assert "subprocess failed" in res.detail


async def test_plan_falls_back_on_non_json():
    """If the runner returns plain text (not JSON), plan still returns something."""
    async def plan_runner(instr, target, model):
        return "plain text plan"

    coder = ClaudeCodeCoder(plan_runner=plan_runner)
    plan = await coder.plan(CodingInstruction("u1", "do it"), "/repo")
    assert "plain text plan" in plan.summary


# ---------------------------------------------------------------------------
# Fix 2 — git commit failure returns ok=False
# ---------------------------------------------------------------------------

async def test_execute_git_commit_failure_returns_not_ok():
    """If git commit fails (e.g. no user identity), execute returns ok=False.

    Approach: real git repo with no HEAD + git_env overriding identity to empty
    strings so git refuses to commit.  exec_runner is injected to "succeed" so
    the failure comes purely from _collect_git.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        subprocess.run(["git", "init", tmpdir], check=True, capture_output=True)

        # Write a dirty file so status --porcelain is non-empty.
        dirty = os.path.join(tmpdir, "new_file.txt")
        with open(dirty, "w") as f:
            f.write("hello\n")

        # Strip all identity env vars so git refuses to commit.
        no_identity_env = {
            k: v for k, v in os.environ.items()
            if k not in {"GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
                         "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"}
        }
        no_identity_env["GIT_AUTHOR_NAME"] = ""
        no_identity_env["GIT_AUTHOR_EMAIL"] = ""
        no_identity_env["GIT_COMMITTER_NAME"] = ""
        no_identity_env["GIT_COMMITTER_EMAIL"] = ""
        # Also prevent git from reading global config user identity.
        no_identity_env["GIT_CONFIG_NOSYSTEM"] = "1"
        no_identity_env["HOME"] = tmpdir  # no ~/.gitconfig with real identity

        async def exec_runner(instr, target, model):
            return '{"result": "done", "is_error": false}'

        coder = ClaudeCodeCoder(exec_runner=exec_runner, git_env=no_identity_env)
        plan = CodingPlan("s", tmpdir, "add file")
        result = await coder.execute(plan, "ari/tg-test")

        assert result.ok is False, (
            "Expected ok=False when git commit fails due to missing identity"
        )
        assert result.detail, "Expected non-empty detail on commit failure"


# ---------------------------------------------------------------------------
# Slow integration test — requires real `claude` CLI + git on PATH
# ---------------------------------------------------------------------------

@pytest.mark.slow
async def test_execute_real_cli_creates_file():
    """Integration: git-init a temp repo, create a branch, run execute with
    real `claude` CLI.  Asserts hello.txt is created and a new commit exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Set up a real git repo
        subprocess.run(["git", "init", tmpdir], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", tmpdir, "config", "user.email", "test@test.com"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", tmpdir, "config", "user.name", "Test"],
            check=True, capture_output=True,
        )
        # Initial commit so HEAD exists
        readme = os.path.join(tmpdir, "README.md")
        with open(readme, "w") as f:
            f.write("init\n")
        subprocess.run(["git", "-C", tmpdir, "add", "."], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", tmpdir, "commit", "-m", "initial"],
            check=True, capture_output=True,
        )
        # Create a branch
        subprocess.run(
            ["git", "-C", tmpdir, "checkout", "-b", "ari/tg-slow"],
            check=True, capture_output=True,
        )

        coder = ClaudeCodeCoder()
        plan = CodingPlan(
            summary="create hello.txt",
            target_dir=tmpdir,
            instruction_text="create a file called hello.txt containing the word pong",
        )
        result = await coder.execute(plan, "ari/tg-slow")

        assert result.ok, f"execute failed: {result.detail}"
        hello_path = os.path.join(tmpdir, "hello.txt")
        assert os.path.exists(hello_path), "hello.txt was not created"
        with open(hello_path) as f:
            content = f.read()
        assert "pong" in content.lower(), f"pong not in {content!r}"

        # At least one commit after the initial
        log = subprocess.run(
            ["git", "-C", tmpdir, "log", "--oneline"],
            capture_output=True, text=True, check=True,
        )
        lines = [l for l in log.stdout.strip().splitlines() if l]
        assert len(lines) >= 2, f"Expected at least 2 commits, got: {log.stdout}"


async def test_default_runner_passes_cli_env(monkeypatch):
    import ari.infrastructure.coder.claude_code_coder as mod
    captured = {}

    async def fake_run_streaming(argv, cwd=None, timeout=None, env=None, **_kw):
        captured["env"] = env
        return '{"type": "result", "is_error": false, "result": "plan"}'

    monkeypatch.setattr(mod, "run_streaming", fake_run_streaming)
    coder = ClaudeCodeCoder(claude_bin="claude", cli_env={"CLAUDE_CONFIG_DIR": "x"})
    await coder.plan(CodingInstruction("u", "do it", None), ".")
    assert captured["env"] == {"CLAUDE_CONFIG_DIR": "x"}


async def test_plan_and_exec_argv_are_isolated(monkeypatch):
    import ari.infrastructure.coder.claude_code_coder as mod
    seen = []

    async def fake_run_streaming(argv, cwd=None, timeout=None, env=None, **_kw):
        seen.append(argv)
        return '{"type": "result", "is_error": false, "result": "ok"}'

    monkeypatch.setattr(mod, "run_streaming", fake_run_streaming)
    coder = ClaudeCodeCoder(claude_bin="claude")
    await coder._default_plan_runner("x", ".", "m")
    await coder._default_exec_runner("x", ".", "m")
    assert len(seen) == 2
    for argv in seen:
        assert "--strict-mcp-config" in argv
        assert argv[argv.index("--setting-sources") + 1] == "project"
