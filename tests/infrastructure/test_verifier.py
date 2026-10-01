"""Tests for CoderVerifier.

All tests use an injectable fake runner to avoid shelling out to uv/pytest.
"""
from ari.infrastructure.coder.verifier import CoderVerifier, VerifyResult

# ---------------------------------------------------------------------------
# Fake runner helpers
# ---------------------------------------------------------------------------


def make_runner(*responses: tuple[str, str, int]):
    """Return a fake async runner that yields scripted (stdout, stderr, rc) responses.

    Recorded calls are available via runner.calls as a list of argv lists.
    """
    calls: list[list[str]] = []

    async def runner(argv: list[str], cwd: str) -> tuple[str, str, int]:
        calls.append(list(argv))
        idx = len(calls) - 1
        if idx < len(responses):
            return responses[idx]
        raise AssertionError(f"Unexpected call #{idx + 1}: {argv}")

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_green_when_tests_pass():
    """rc 0 → ok True, detail empty string."""
    runner = make_runner(("all passed", "", 0))
    verifier = CoderVerifier(runner=runner)
    result = await verifier.verify("/repo")
    assert result == VerifyResult(ok=True, detail="")


async def test_red_when_tests_fail():
    """rc != 0 with stderr → ok False, detail contains part of the output."""
    stderr = "FAILED tests/test_foo.py::test_bar - AssertionError"
    runner = make_runner(("", stderr, 1))
    verifier = CoderVerifier(runner=runner)
    result = await verifier.verify("/repo")
    assert result.ok is False
    assert "AssertionError" in result.detail


async def test_installs_when_deps_changed():
    """deps_changed=True runs install_cmd BEFORE test_cmd; both rc 0 → ok True."""
    install_cmd = ("uv", "pip", "install", "-e", ".[dev]")
    test_cmd = ("uv", "run", "pytest", "-m", "not slow", "-q")
    runner = make_runner(
        ("install ok", "", 0),   # install_cmd call
        ("all passed", "", 0),   # test_cmd call
    )
    verifier = CoderVerifier(test_cmd=test_cmd, install_cmd=install_cmd, runner=runner)
    result = await verifier.verify("/repo", deps_changed=True)

    assert result == VerifyResult(ok=True, detail="")
    assert len(runner.calls) == 2
    # First call must be the install command
    assert runner.calls[0] == list(install_cmd)
    # Second call must be the test command
    assert runner.calls[1] == list(test_cmd)


async def test_install_failure_skips_tests():
    """deps_changed=True, install rc != 0 → ok False and test_cmd never invoked."""
    install_cmd = ("uv", "pip", "install", "-e", ".[dev]")
    test_cmd = ("uv", "run", "pytest", "-q")
    stderr = "ERROR: could not find package"
    runner = make_runner(("", stderr, 1))  # only one call expected
    verifier = CoderVerifier(test_cmd=test_cmd, install_cmd=install_cmd, runner=runner)
    result = await verifier.verify("/repo", deps_changed=True)

    assert result.ok is False
    assert "could not find package" in result.detail
    # test_cmd must NOT have been invoked
    assert len(runner.calls) == 1
    assert runner.calls[0] == list(install_cmd)


async def test_no_install_when_deps_unchanged():
    """deps_changed=False → install_cmd never invoked; only test_cmd runs."""
    install_cmd = ("uv", "pip", "install", "-e", ".[dev]")
    test_cmd = ("uv", "run", "pytest", "-q")
    runner = make_runner(("passed", "", 0))
    verifier = CoderVerifier(test_cmd=test_cmd, install_cmd=install_cmd, runner=runner)
    result = await verifier.verify("/repo", deps_changed=False)

    assert result.ok is True
    assert len(runner.calls) == 1
    assert runner.calls[0] == list(test_cmd)


async def test_runner_exception_returns_not_ok():
    """If the runner raises, verify returns ok=False and does not propagate."""

    async def exploding_runner(argv: list[str], cwd: str) -> tuple[str, str, int]:
        raise RuntimeError("subprocess exploded")

    verifier = CoderVerifier(runner=exploding_runner)
    result = await verifier.verify("/repo")

    assert result.ok is False
    assert "subprocess exploded" in result.detail
