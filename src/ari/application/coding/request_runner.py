import contextlib
import logging
from datetime import timedelta

from ari.domain.coding.requests import DONE, FAILED, SKIPPED

log = logging.getLogger("ari.coding_requests")

STALE = "Descarté una propuesta de código vieja: {}"
BUSY = "Ya tengo un trabajo o plan de código pendiente; respóndelo primero."
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
        self._planning: set[str] = set()  # owners with a plan being prepared

    async def __call__(self) -> None:
        for req in await self._requests.claim_pending():
            if self._clock() - req.created_at > self._max_age:
                await self._requests.finish(req.id, SKIPPED, "stale")
                await self._send(req.chat_id, STALE.format(req.instruction))
                continue
            if (req.user_id in self._planning or self._pending.is_busy(req.user_id)
                    or self._pending.get(req.user_id) is not None):
                await self._requests.finish(req.id, SKIPPED, "busy")
                await self._send(req.chat_id, BUSY)
                continue
            self._planning.add(req.user_id)
            self._spawn(self._plan(req))

    async def _plan(self, req) -> None:
        try:
            progress = (self._progress(req.chat_id) if self._progress
                        else contextlib.nullcontext())
            async with progress:
                reply = await self._request_coding(req.user_id, req.instruction, req.target)
        except Exception as exc:  # noqa: BLE001 — report and keep the runner alive
            log.exception("planning coding request %s failed", req.id)
            await self._requests.finish(req.id, FAILED, str(exc)[:300])
            await self._send(req.chat_id, FAILED_MSG.format(str(exc)[:200]))
            return
        finally:
            self._planning.discard(req.user_id)
        await self._requests.finish(req.id, DONE)
        await self._send(req.chat_id, reply)
