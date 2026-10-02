import logging
import os
import re

log = logging.getLogger("ari.confirm_coding")

_DEPS_FILES = {"pyproject.toml", "requirements.txt", "uv.lock", "setup.cfg", "setup.py"}


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:24] or "task")


def _is_deps_file(path: str) -> bool:
    return os.path.basename(path) in _DEPS_FILES


class ConfirmCoding:
    def __init__(self, coder, workspace, pending_store, slug_source=None,
                 verifier=None, on_merged=None):
        self._coder = coder
        self._ws = workspace
        self._store = pending_store
        self._n = 0
        self._slug_source = slug_source
        self._use_custom_slug = slug_source is not None
        self._verifier = verifier
        self._on_merged = on_merged

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

            base = await self._ws.current_branch(pending.plan.target_dir)
            branch = await self._ws.create_branch(pending.plan.target_dir, slug)
            await report(f"Arranco en branch `{branch}`…")
            result = await self._coder.execute(pending.plan, branch)

            if not result.ok:
                await report(f"El trabajo en `{branch}` falló: {result.detail[:300]}. "
                             "Dejé el branch para que lo revises.")
                return

            if self._verifier is None:
                files = ", ".join(result.changed_files) or "(sin cambios)"
                commits = ", ".join(result.commits) or "(sin commits)"
                await report(f"Listo en `{branch}`. Archivos: {files}. Commits: {commits}. "
                             "El push/PR/merge quedan para ti.")
                return

            # --- verifier path ---
            deps_changed = any(_is_deps_file(f) for f in result.changed_files)
            await report("Verificando con los tests…")
            verdict = await self._verifier.verify(pending.plan.target_dir, deps_changed)

            if not verdict.ok:
                try:
                    await self._ws.checkout(pending.plan.target_dir, base)
                except Exception:
                    log.exception("checkout back to base failed after red tests")
                await report(
                    f"❌ Tests en rojo, no mergeo. Dejé `{branch}` para revisar.\n"
                    f"{verdict.detail[:500]}"
                )
                return

            # verdict.ok → merge
            await self._ws.merge_into(pending.plan.target_dir, base, branch)
            reloaded: list[str] = []
            if self._on_merged is not None:
                try:
                    reloaded = (await self._on_merged()) or []
                except Exception:
                    log.exception("on_merged callback raised")
                    reloaded = []

            files = ", ".join(result.changed_files) or "(sin cambios)"
            extra = f" Skills activos: {', '.join(reloaded)}." if reloaded else ""
            await report(f"✅ Verde. Mergeado a `{base}`. Archivos: {files}.{extra}")

        except Exception as exc:
            log.exception("coding execution failed")
            await report(f"Error ejecutando la tarea: {str(exc)[:300]}")
        finally:
            self._store.clear_busy(user_id)
