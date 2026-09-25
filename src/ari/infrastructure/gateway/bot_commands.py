import logging

from telegram import BotCommand, BotCommandScopeChat, BotCommandScopeDefault

from ari.domain.agent.capabilities import menu_commands

log = logging.getLogger("ari.telegram")

# Derived from the capability registry, the same list Ari's prompt is built from.
PUBLIC_COMMANDS = menu_commands(owner=False)
OWNER_COMMANDS = menu_commands(owner=True)


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
