import logging

log = logging.getLogger("ari.confirm_command")

_MAX_OUTPUT = 3000


def _format(command: str, result) -> str:
    head = f"`{command}`"
    if result.timed_out:
        return f"{head} — ⏱️ se pasó del tiempo límite y lo corté."
    body = (result.stdout or "")
    if result.stderr:
        body = f"{body}\n{result.stderr}" if body else result.stderr
    body = body.strip() or "(sin salida)"
    if len(body) > _MAX_OUTPUT:
        body = body[:_MAX_OUTPUT] + "\n… (salida recortada)"
    status = "✅" if result.returncode == 0 else f"❌ (exit {result.returncode})"
    return f"{head} {status}\n```\n{body}\n```"


class ConfirmCommand:
    """Runs a shell command the owner confirmed with «dale» and reports its output.
    ``run`` is an async (command, cwd) -> CommandResult port; busy is always
    cleared so a crash can never leave the user locked out."""

    def __init__(self, run, pending_store, cwd: str):
        self._run = run
        self._store = pending_store
        self._cwd = cwd

    async def __call__(self, user_id: str, action, report) -> None:
        try:
            result = await self._run(action.command, self._cwd)
            await report(_format(action.command, result))
        except Exception as exc:
            log.exception("command execution failed")
            await report(f"Error ejecutando el comando: {str(exc)[:300]}")
        finally:
            self._store.clear_busy(user_id)
