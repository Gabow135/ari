"""Background runner for missions: claims PENDING, executes via the message handler,
notifies the user. Plugs into the Scheduler like RunDueItems."""
import asyncio
import contextlib
import logging

from ari.domain.missions.entities import CANCELLED, DONE, FAILED, MAX_FAILURES, PAUSED, Mission
from ari.domain.ports.gateway_port import IncomingMessage

log = logging.getLogger("ari.missions")

_PREFIX = "🎯 Misión"
_RETRY_NOTICE = ("Hubo un error en la misión #{id}. La reintento en el próximo ciclo "
                 "({failures}/{max} fallos).")
_PAUSE_NOTICE = ("⚠️ La misión #{id} falló {max} veces y quedó pausada.\n"
                 "Instrucción: {instruction}\nÚltimo error: {error}")


class MissionRunner:
    """One scheduler tick: claims all PENDING missions and runs them in background."""

    def __init__(self, missions, handler, send, spawn, pool=None):
        """
        missions : SqliteMissions
        handler  : HandleMessage (callable)
        send     : async (chat_id: str, text: str) -> None — never raises
        spawn    : (coro) -> None — fire-and-forget wrapper
        pool     : AgentPool | None — caps concurrent heavy mission turns
        """
        self._missions = missions
        self._handler = handler
        self._send = send
        self._spawn = spawn
        self._pool = pool

    async def __call__(self) -> None:
        pending = await self._missions.claim_pending()
        for mission in pending:
            self._spawn(self._execute(mission))

    async def _execute(self, mission: Mission) -> None:
        try:
            incoming = IncomingMessage(
                user_id=mission.user_id,
                chat_id=mission.chat_id,
                text=mission.instruction,
            )
            async with (self._pool or contextlib.nullcontext()):
                out = await self._handler(incoming, allow_actions=True)
            result = out.text or "(sin resultado)"
            await self._missions.finish(mission.id, DONE, result)
            await self._send(
                mission.chat_id,
                f"{_PREFIX} #{mission.id} completada:\n{result}",
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("mission #%s failed", mission.id)
            failures = await self._missions.increment_failures(mission.id)
            if failures >= MAX_FAILURES:
                await self._missions.finish(mission.id, PAUSED,
                                            result=f"Pausada tras {failures} fallos.")
                await self._send(
                    mission.chat_id,
                    _PAUSE_NOTICE.format(id=mission.id, max=MAX_FAILURES,
                                        instruction=mission.instruction,
                                        error=str(exc)[:200]),
                )
            else:
                # Re-queue as PENDING so the next tick retries it.
                await self._missions.set_status(mission.id, "pending")
                await self._send(
                    mission.chat_id,
                    _RETRY_NOTICE.format(id=mission.id, failures=failures,
                                        max=MAX_FAILURES),
                )
