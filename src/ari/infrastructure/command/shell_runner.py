import asyncio

from ari.domain.command.entities import CommandResult


class ShellRunner:
    """Runs a shell command on the host and captures its output. Host-level by
    design (owner-only, «dale»-gated upstream); cwd defaults to the Ari repo.
    A command past ``timeout`` seconds is killed and flagged ``timed_out``."""

    def __init__(self, timeout: float = 120.0):
        self._timeout = timeout

    async def __call__(self, command: str, cwd: str) -> CommandResult:
        proc = await asyncio.create_subprocess_shell(
            command, cwd=cwd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return CommandResult(returncode=-1, timed_out=True)
        return CommandResult(
            returncode=proc.returncode if proc.returncode is not None else -1,
            stdout=out.decode(errors="replace"),
            stderr=err.decode(errors="replace"))
