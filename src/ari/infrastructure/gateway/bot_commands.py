import logging

from telegram import BotCommand, BotCommandScopeChat, BotCommandScopeDefault

log = logging.getLogger("ari.telegram")

PUBLIC_COMMANDS = [
    ("start", "Empezar / pedir acceso"),
]
OWNER_COMMANDS = PUBLIC_COMMANDS + [
    ("code", "Tarea de código: /code [dir:ruta] instrucción"),
    ("aprobar", "Aprobar acceso: /aprobar CÓDIGO"),
    ("revocar", "Quitar acceso: /revocar USER_ID"),
    ("accesos", "Ver solicitudes y accesos"),
    ("restart", "Reiniciar Ari"),
    ("stop", "Apagar Ari"),
]


def _commands(pairs) -> list[BotCommand]:
    return [BotCommand(name, description) for name, description in pairs]


async def register_commands(bot, owner_ids: set[str]) -> None:
    """Publish Telegram's "/" menu: ``/start`` for everyone, the full set in each
    owner's private chat. Best-effort: a failure is logged, never fatal."""
    targets = [(PUBLIC_COMMANDS, BotCommandScopeDefault())]
    targets += [(OWNER_COMMANDS, BotCommandScopeChat(int(o))) for o in sorted(owner_ids)]
    for pairs, scope in targets:
        try:
            await bot.set_my_commands(_commands(pairs), scope=scope)
        except Exception as exc:  # noqa: BLE001 — menu is cosmetic
            log.warning("could not set command menu for %s: %s", scope, exc)
