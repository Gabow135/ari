import logging
from datetime import timedelta

from ari.application.text_format import truncate
from ari.domain.command.entities import PendingCommand
from ari.domain.command.requests import DONE, SKIPPED

log = logging.getLogger("ari.command_requests")

STALE = "Descarté un comando viejo: {}"
BUSY = "No preparé «{}»: ya tengo un comando o trabajo en curso; respóndelo primero."
PROPOSAL = ("Voy a correr en tu máquina:\n`{}`\n\n"
            "Responde *dale* para ejecutar, o *no* para cancelar.")


class CommandRequestRunner:
    """Turns shell-command proposals queued by Ari's MCP server (proponer_comando)
    into a pending command awaiting the owner's «dale». Unlike coding there is no
    slow planning step, so the pending command is stored inline."""

    def __init__(self, requests, pending_store, send, clock,
                 max_age: timedelta = timedelta(hours=1)):
        self._requests, self._pending = requests, pending_store
        self._send, self._clock, self._max_age = send, clock, max_age

    async def __call__(self) -> None:
        for req in await self._requests.claim_pending():
            if self._clock() - req.created_at > self._max_age:
                await self._requests.finish(req.id, SKIPPED, "stale")
                await self._send(req.chat_id, STALE.format(truncate(req.command)))
                continue
            # Busy only while a confirmed command is executing. A command still
            # awaiting «dale» is NOT a blocker: the new one supersedes it (newest
            # wins), so the proposal stays honest.
            if self._pending.is_busy(req.user_id):
                await self._requests.finish(req.id, SKIPPED, "busy")
                await self._send(req.chat_id, BUSY.format(truncate(req.command)))
                continue
            # Drop any older, unconfirmed command and propose this one in its place.
            self._pending.clear(req.user_id)
            self._pending.put(req.user_id, PendingCommand(req.command))
            try:
                await self._requests.finish(req.id, DONE)
            except Exception:
                log.exception("could not mark command request %s done", req.id)
            try:
                await self._send(req.chat_id, PROPOSAL.format(req.command))
            except Exception:
                log.exception("could not send command proposal %s", req.id)
