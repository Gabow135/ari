import logging
import re

log = logging.getLogger("ari.confirm_coding")


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:24] or "task")


class ConfirmCoding:
    def __init__(self, coder, workspace, pending_store, slug_source=None):
        self._coder = coder
        self._ws = workspace
        self._store = pending_store
        self._n = 0
        self._slug_source = slug_source
        self._use_custom_slug = slug_source is not None

    def _auto_slug(self) -> str:
        self._n += 1
        return f"{self._n}"

    async def __call__(self, user_id: str, action, report) -> None:
        # action is the already-popped PendingAction; busy is already marked by
        # route_message before this coroutine is scheduled — do NOT re-pop / re-mark.
        pending = action
        try:
            if self._use_custom_slug:
                slug = self._slug_source()
            else:
                slug = f"{_slugify(pending.instruction.text)}-{self._auto_slug()}"
            branch = await self._ws.create_branch(pending.plan.target_dir, slug)
            await report(f"Arranco en branch `{branch}`…")
            result = await self._coder.execute(pending.plan, branch)
            if result.ok:
                files = ", ".join(result.changed_files) or "(sin cambios)"
                commits = ", ".join(result.commits) or "(sin commits)"
                await report(f"Listo en `{branch}`. Archivos: {files}. Commits: {commits}. "
                             "El push/PR/merge quedan para ti.")
            else:
                await report(f"El trabajo en `{branch}` falló: {result.detail[:300]}. "
                             "Dejé el branch para que lo revises.")
        except Exception as exc:
            log.exception("coding execution failed")
            await report(f"Error ejecutando la tarea: {str(exc)[:300]}")
        finally:
            self._store.clear_busy(user_id)
