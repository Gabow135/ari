import logging
import secrets
from dataclasses import dataclass, field

from ari.domain.access.entities import APPROVED, PENDING, AccessRecord

log = logging.getLogger("ari.access")

# No 0/O or 1/I: codes are read aloud / retyped by humans.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_LEN = 8
ADMIN_COMMANDS = ("aprobar", "revocar", "accesos")


def normalize_code(text: str) -> str:
    return "".join(ch for ch in text.upper() if ch.isalnum())


def format_code(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


def _who(rec_user_id: str, username: str | None) -> str:
    return f"@{username} (id {rec_user_id})" if username else f"id {rec_user_id}"


@dataclass
class GateResult:
    """What the gateway must do: ``allowed`` lets the message through; ``reply``
    goes back to the sender; ``notifications`` are (chat_id, text) to send out."""
    allowed: bool = False
    reply: str | None = None
    notifications: list[tuple[str, str]] = field(default_factory=list)


class AccessGate:
    """Only owners and users whose pairing code an owner approved may talk to Ari."""

    def __init__(self, store, owner_ids: set[str]):
        self._store = store
        self._owners = {str(x) for x in owner_ids}

    def is_owner(self, user_id: str) -> bool:
        return str(user_id) in self._owners

    async def check(self, user_id: str, username: str | None) -> GateResult:
        if self.is_owner(user_id):
            return GateResult(allowed=True)
        rec = await self._store.get(user_id)
        if rec is not None and rec.status == APPROVED:
            return GateResult(allowed=True)

        notifications = []
        if rec is None:
            code = await self._new_code()
            await self._store.create_pending(user_id, username, code)
            log.info("access requested by %s", _who(user_id, username))
            note = (f"{_who(user_id, username)} pide acceso a Ari.\n"
                    f"Aprobá con: /aprobar {format_code(code)}")
            notifications = [(owner, note) for owner in sorted(self._owners)]
        else:
            code = rec.code
        return GateResult(
            reply=("No tenés acceso todavía. Tu código es:\n\n"
                   f"{format_code(code)}\n\n"
                   "Pasáselo al administrador para que te habilite."),
            notifications=notifications,
        )

    async def admin_command(self, text: str, user_id: str) -> GateResult:
        parts = text.strip().split(maxsplit=1)
        cmd = parts[0].lstrip("/").split("@")[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        if not self.is_owner(user_id):
            return GateResult(reply="Ese comando es solo para el dueño.")
        if cmd == "aprobar":
            return await self._approve(arg)
        if cmd == "revocar":
            return await self._revoke(arg)
        return await self._list()

    async def _approve(self, arg: str) -> GateResult:
        if not arg:
            return GateResult(reply="Uso: /aprobar CÓDIGO")
        rec = await self._store.find_by_code(normalize_code(arg))
        if rec is None:
            return GateResult(reply="Código no encontrado.")
        if rec.status == APPROVED:
            return GateResult(reply=f"{_who(rec.user_id, rec.username)} ya tenía acceso.")
        await self._store.set_status(rec.user_id, APPROVED)
        log.info("access approved for %s", _who(rec.user_id, rec.username))
        return GateResult(
            reply=f"Listo, {_who(rec.user_id, rec.username)} ya tiene acceso.",
            notifications=[(rec.user_id, "¡Ya tenés acceso! Escribime cuando quieras.")],
        )

    async def _revoke(self, arg: str) -> GateResult:
        if not arg:
            return GateResult(reply="Uso: /revocar USER_ID")
        rec = await self._store.get(arg)
        if rec is None:
            return GateResult(reply=f"No hay registro de acceso para id {arg}.")
        await self._store.delete(rec.user_id)
        log.info("access revoked for %s", _who(rec.user_id, rec.username))
        return GateResult(reply=f"Acceso revocado para {_who(rec.user_id, rec.username)}.")

    async def _list(self) -> GateResult:
        records = await self._store.list_all()
        if not records:
            return GateResult(reply="No hay solicitudes de acceso.")
        return GateResult(reply="\n".join(_line(r) for r in records))

    async def _new_code(self) -> str:
        while True:
            code = "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LEN))
            if await self._store.find_by_code(code) is None:
                return code


async def deliver(result: GateResult, reply, send) -> None:
    """Send ``result.reply`` via ``reply(text)`` and each notification via
    ``send(chat_id, text)``. A failed notification (e.g. the owner never opened
    a chat with the bot) is logged and does not block the rest."""
    if result.reply:
        await reply(result.reply)
    for chat_id, text in result.notifications:
        try:
            await send(chat_id, text)
        except Exception as exc:  # noqa: BLE001 — best-effort notification
            log.warning("could not notify %s: %s", chat_id, exc)


def _line(rec: AccessRecord) -> str:
    if rec.status == PENDING:
        return f"⏳ {_who(rec.user_id, rec.username)} — pendiente, código {format_code(rec.code)}"
    return f"✅ {_who(rec.user_id, rec.username)} — aprobado"
