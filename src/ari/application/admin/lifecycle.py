import time
from dataclasses import dataclass

from ari.application.coding.command_router import is_affirmative

STOP = "stop"
RESTART = "restart"
LIFECYCLE_COMMANDS = (STOP, RESTART)

_ASK = {
    STOP: "¿Apago Ari? Respondé «dale» en 60 s para confirmar.",
    RESTART: "¿Reinicio Ari? Respondé «dale» en 60 s para confirmar.",
}
_GO = {STOP: "Apagando Ari…", RESTART: "Reiniciando…"}


@dataclass
class Confirmation:
    """``handled``: the message was a reply to a pending /stop or /restart and
    must not be routed further. ``action`` is set only when confirmed."""
    handled: bool = False
    action: str | None = None
    reply: str | None = None


class Lifecycle:
    """Owner-only /stop and /restart, each gated by an explicit confirmation."""

    def __init__(self, is_owner, clock=time.monotonic, ttl: float = 60):
        self._is_owner = is_owner
        self._clock = clock
        self._ttl = ttl
        self._pending: dict[str, tuple[str, float]] = {}

    def request(self, action: str, user_id: str) -> str:
        if not self._is_owner(user_id):
            return "Ese comando es solo para el dueño."
        self._pending[user_id] = (action, self._clock() + self._ttl)
        return _ASK[action]

    def confirm(self, text: str, user_id: str) -> Confirmation:
        pending = self._pending.pop(user_id, None)
        if pending is None:
            return Confirmation()
        action, deadline = pending
        if self._clock() > deadline:
            if is_affirmative(text):
                return Confirmation(
                    handled=True, reply=f"La confirmación venció; mandá /{action} de nuevo.")
            return Confirmation()
        if is_affirmative(text):
            return Confirmation(handled=True, action=action, reply=_GO[action])
        return Confirmation(handled=True, reply="Cancelado.")
