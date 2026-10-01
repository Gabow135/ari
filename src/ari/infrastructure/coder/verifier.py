"""CoderVerifier — installs deps and runs the fast test suite in a target repo."""
import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger("ari.coder.verifier")

_TAIL_CHARS = 800


def _tail(stdout: str, stderr: str) -> str:
    """Return the last _TAIL_CHARS characters of combined output, stderr preferred."""
    combined = (stderr.strip() or stdout.strip())
    return combined[-_TAIL_CHARS:].strip()


@dataclass(frozen=True, slots=True)
class VerifyResult:
    ok: bool
    detail: str = ""


async def _default_runner(argv: list[str], cwd: str) -> tuple[str, str, int]:
    """Default runner: spawns a subprocess with asyncio and enforces a hard timeout.

    Returns (stdout, stderr, returncode).  On TimeoutError the returncode is -1
    and stderr describes the timeout so verify() can report not-ok.
    """
    p = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    try:
        raw_out, raw_err = await asyncio.wait_for(p.communicate(), timeout=900)
    except TimeoutError:
        try:
            p.kill()
        except ProcessLookupError:
            pass
        return "", f"process timed out after 900s: {argv}", -1

    stdout = raw_out.decode(errors="replace").strip()
    stderr = raw_err.decode(errors="replace").strip()
    return stdout, stderr, p.returncode


class CoderVerifier:
    """Installs project dependencies and runs the fast test suite.

    Args:
        test_cmd: Command tuple for running the test suite.
        install_cmd: Command tuple for installing dependencies.
        timeout: Maximum seconds for the default subprocess runner.
        runner: Injectable async callable ``(argv: list[str], cwd: str)
            -> tuple[str, str, int]``.  Defaults to the real subprocess runner.
    """

    def __init__(
        self,
        test_cmd: tuple[str, ...] = ("uv", "run", "pytest", "-m", "not slow", "-q"),
        install_cmd: tuple[str, ...] = ("uv", "pip", "install", "-e", ".[dev]"),
        timeout: int = 900,
        runner: Callable | None = None,
    ) -> None:
        self._test_cmd = test_cmd
        self._install_cmd = install_cmd
        self._timeout = timeout
        self._runner = runner if runner is not None else _default_runner

    async def verify(self, target_dir: str, deps_changed: bool = False) -> VerifyResult:
        """Install deps (when changed) then run the test suite.

        Args:
            target_dir: Absolute path to the target repository root.
            deps_changed: When True, run install_cmd before the test suite.
                If the install fails, tests are NOT run and a failing result
                is returned immediately.

        Returns:
            VerifyResult with ok=True when the test suite exits 0, False otherwise.
        """
        try:
            if deps_changed:
                stdout, stderr, rc = await self._runner(list(self._install_cmd), target_dir)
                if rc != 0:
                    log.warning("install failed (rc=%d): %s", rc, stderr[:200])
                    return VerifyResult(ok=False, detail=_tail(stdout, stderr))

            stdout, stderr, rc = await self._runner(list(self._test_cmd), target_dir)
            if rc == 0:
                return VerifyResult(ok=True, detail="")
            log.warning("tests failed (rc=%d): %s", rc, stderr[:200])
            return VerifyResult(ok=False, detail=_tail(stdout, stderr))
        except Exception as exc:  # noqa: BLE001 — spec: never propagate runner exceptions
            log.error("runner raised: %s", exc)
            detail = str(exc)[-_TAIL_CHARS:].strip()
            return VerifyResult(ok=False, detail=detail)
