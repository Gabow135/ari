from telegram import BotCommandScopeChat, BotCommandScopeDefault

from ari.infrastructure.gateway.bot_commands import register_commands


class _FakeBot:
    def __init__(self, fail_for=()):
        self.calls, self._fail_for = [], set(fail_for)

    async def set_my_commands(self, commands, scope):
        if getattr(scope, "chat_id", None) in self._fail_for:
            raise RuntimeError("Bad Request: chat not found")
        self.calls.append(([c.command for c in commands], scope))


async def test_default_menu_only_has_start_and_owners_get_full_menu():
    bot = _FakeBot()
    await register_commands(bot, {"42"})
    (public, default_scope), (owner, owner_scope) = bot.calls
    assert public == ["start", "recordatorios"] and isinstance(default_scope, BotCommandScopeDefault)
    assert isinstance(owner_scope, BotCommandScopeChat) and owner_scope.chat_id == 42
    assert {"start", "recordatorios", "code", "aprobar", "revocar", "accesos",
            "restart", "stop"} == set(owner)


async def test_failure_for_one_owner_does_not_stop_the_rest():
    bot = _FakeBot(fail_for={7})
    await register_commands(bot, {"7", "42"})
    assert [getattr(s, "chat_id", None) for _, s in bot.calls] == [None, 42]
