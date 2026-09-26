import asyncio
import contextlib
import logging
from datetime import timedelta

from ari.application.text_format import truncate
from ari.domain.coding.requests import DONE, FAILED, SKIPPED

log = logging.getLogger("ari.coding_requests")

STALE = "Descarté una propuesta de código vieja: {}"
BUSY = "No preparé «{}»: ya tengo un trabajo o plan de código pendiente; respóndelo primero."
FAILED_MSG = "No pude preparar el plan: {}"


class CodingRequestRunner:
    """Turns code proposals queued by Ari's MCP server into the usual /code plan.
    Claiming and dispatching are quick; planning runs in the background so the
    chat reply (this runs from the post-turn hook) is never held up."""

    def __init__(self, requests, request_coding, pending_store, send, spawn, clock,
                 progress=None, max_age: timedelta = timedelta(hours=1)):
        self._requests, self._request_coding = requests, request_coding
        self._pending, self._send, self._spawn = pending_store, send, spawn
        self._clock, self._progress, self._max_age = clock, progress, max_age

    async def __call__(self) -> None:
        for req in await self._requests.claim_pending():
            if self._clock() - req.created_at > self._max_age:
                await self._requests.finish(req.id, SKIPPED, "stale")
                await self._send(req.chat_id, STALE.format(req.instruction))
                continue
            already_busy = (self._pending.is_busy(req.user_id)
                            or self._pending.get(req.user_id) is not None)
            if already_busy or not self._pending.mark_planning(req.user_id):
                await self._requests.finish(req.id, SKIPPED, "busy")
                await self._send(req.chat_id, BUSY.format(truncate(req.instruction)))
                continue
            self._spawn(self._plan(req))

    async def _plan(self, req) -> None:
        try:
            progress = (self._progress(req.chat_id) if self._progress
                        else contextlib.nullcontext())
            async with progress:
                reply = await self._request_coding(req.user_id, req.instruction, req.target,
                                                    proposed=True)
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await self._requests.finish(req.id, FAILED, "interrumpido")
            raise
        except Exception as exc:
            log.exception("planning coding request %s failed", req.id)
            try:
                await self._requests.finish(req.id, FAILED, str(exc)[:300])
            except Exception:
                log.exception("could not mark coding request %s failed", req.id)
            try:
                await self._send(req.chat_id, FAILED_MSG.format(str(exc)[:200]))
            except Exception:
                log.exception("could not send error message for coding request %s", req.id)
            return
        finally:
            self._pending.clear_planning(req.user_id)
        try:
            await self._requests.finish(req.id, DONE)
        except Exception:
            log.exception("could not mark coding request %s done", req.id)
        try:
            await self._send(req.chat_id, reply)
        except Exception:
            log.exception("could not send plan for coding request %s", req.id)
