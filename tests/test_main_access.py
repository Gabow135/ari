"""Wiring test: main() must run the access gate before routing any message."""
from types import SimpleNamespace

import pytest
import telegram.ext

import ari.main as main_mod
from ari.application.access.gate import AccessGate
from ari.infrastructure.access.sqlite_access_store import SqliteAccessStore
from ari.infrastructure.persistence.db import connect


class _FakeApp:
    def __init__(self):
        self.handlers, self.bot_data, self.sent = [], {}, []

        async def send_message(chat_id, text):
            self.sent.append((chat_id, text))

        self.bot = SimpleNamespace(send_message=send_message)

    def add_handler(self, h):
        self.handlers.append(h)

    def run_polling(self):
        pass


class _FakeBuilder:
    def __init__(self, app):
        self._app = app

    def token(self, _t):
        return self

    def post_init(self, _f):
        return self

    def post_shutdown(self, _f):
        return self

    def build(self):
        return self._app


def _update(user_id, text, replies):
    async def reply_text(t):
        replies.append(t)

    msg = SimpleNamespace(
        text=text, chat_id=user_id, reply_text=reply_text,
        from_user=SimpleNamespace(id=user_id, username="juan"))
    return SimpleNamespace(effective_message=msg, message=msg)


@pytest.fixture
async def wired(monkeypatch):
    app = _FakeApp()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setattr(telegram.ext.Application, "builder", lambda: _FakeBuilder(app))
    routed = []

    async def fake_route(text, user_id, deps):
        routed.append((text, user_id))
        return "respuesta"

    monkeypatch.setattr(main_mod, "route_message", fake_route)
    main_mod.main()
    conn = await connect(":memory:", embedding_dim=4)
    app.bot_data.update(
        gate=AccessGate(SqliteAccessStore(conn), owner_ids={"42"}),
        handler=None, confirm=None,
        coding_deps=main_mod.CodingDeps(None, None, None, None, None, None))
    yield app, routed
    await conn.close()


def _callback(app, handler_type, command=None):
    for h in app.handlers:
        if isinstance(h, handler_type) and (command is None or command in h.commands):
            return h.callback
    raise AssertionError("handler not registered")


async def test_unapproved_message_is_blocked_before_routing(wired):
    app, routed = wired
    on_message = _callback(app, telegram.ext.MessageHandler)
    replies = []
    await on_message(_update(7, "hola", replies), None)
    assert routed == []
    assert "código" in replies[0].lower()
    assert app.sent and app.sent[0][0] == 42  # owner notified


async def test_unapproved_code_command_is_blocked(wired):
    app, routed = wired
    replies = []
    await _callback(app, telegram.ext.CommandHandler, "code")(
        _update(7, "/code add X", replies), None)
    assert routed == []


async def test_approved_user_reaches_routing(wired):
    app, routed = wired
    on_message = _callback(app, telegram.ext.MessageHandler)
    replies = []
    await on_message(_update(7, "hola", replies), None)
    code = replies[0].split("\n\n")[1]
    await _callback(app, telegram.ext.CommandHandler, "aprobar")(
        _update(42, f"/aprobar {code}", []), None)
    await on_message(_update(7, "hola otra vez", replies), None)
    assert routed == [("hola otra vez", "7")]
