"""Wiring test: main() must run the access gate before routing any message."""
import asyncio
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

        self.script = None  # async fn(app) run inside run_polling, like live updates
        self.stopped = False

    def add_handler(self, h):
        self.handlers.append(h)

    def stop_running(self):
        self.stopped = True

    def run_polling(self):
        if self.script is not None:
            asyncio.run(self.script(self))


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


# --- /stop and /restart -----------------------------------------------------

def _run_main_with(monkeypatch, steps):
    """Run main() with a fake app whose polling loop feeds ``steps``: a list of
    (command_or_None, user_id, text). Returns (app, relaunches, replies)."""
    app = _FakeApp()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("ARI_OWNER_IDS", "42")
    monkeypatch.setattr(telegram.ext.Application, "builder", lambda: _FakeBuilder(app))
    relaunches, replies = [], []
    monkeypatch.setattr(main_mod, "relaunch", lambda chat: relaunches.append(chat))

    async def fake_route(text, user_id, deps):
        return "chat"

    monkeypatch.setattr(main_mod, "route_message", fake_route)

    async def script(a):
        conn = await connect(":memory:", embedding_dim=4)
        a.bot_data.update(
            gate=AccessGate(SqliteAccessStore(conn), owner_ids={"42"}),
            handler=None, confirm=None,
            coding_deps=main_mod.CodingDeps(None, None, None, None, None, None))
        for command, uid, text in steps:
            if command is None:
                cb = _callback(a, telegram.ext.MessageHandler)
            else:
                cb = _callback(a, telegram.ext.CommandHandler, command)
            await cb(_update(uid, text, replies), None)
        await conn.close()

    app.script = script
    main_mod.main()
    return app, relaunches, replies


def test_stop_needs_confirmation(monkeypatch):
    app, relaunches, replies = _run_main_with(monkeypatch, [("stop", 42, "/stop")])
    assert not app.stopped and relaunches == []
    assert "dale" in replies[-1].lower()


def test_confirmed_stop_stops_without_relaunch(monkeypatch):
    app, relaunches, _ = _run_main_with(
        monkeypatch, [("stop", 42, "/stop"), (None, 42, "dale")])
    assert app.stopped and relaunches == []


def test_confirmed_restart_stops_then_relaunches(monkeypatch):
    app, relaunches, _ = _run_main_with(
        monkeypatch, [("restart", 42, "/restart"), (None, 42, "dale")])
    assert app.stopped and relaunches == ["42"]


def test_non_owner_cannot_stop(monkeypatch):
    app, _, replies = _run_main_with(
        monkeypatch, [("stop", 7, "/stop"), (None, 7, "dale")])
    assert not app.stopped


# --- /recordatorios and background sends ------------------------------------

class _FakeActions:
    async def list_text(self, user_id):
        return f"lista de {user_id}"


async def test_recordatorios_requires_access_and_lists(wired):
    app, _ = wired
    app.bot_data["actions"] = _FakeActions()
    cb = _callback(app, telegram.ext.CommandHandler, "recordatorios")
    replies = []
    await cb(_update(7, "/recordatorios", replies), None)
    assert "código" in replies[0].lower()  # not approved yet
    owner_replies = []
    await cb(_update(42, "/recordatorios", owner_replies), None)
    assert owner_replies == ["lista de 42"]


async def test_background_send_splits_long_text(wired):
    app, _ = wired
    await main_mod._send_quietly(app.bot, "42", "x" * 5000)
    assert [len(t) for _, t in app.sent] == [4096, 904]


async def test_background_send_never_raises():
    class Broken:
        async def send_message(self, chat_id, text):
            raise RuntimeError("Forbidden")

    await main_mod._send_quietly(Broken(), "42", "hola")  # must not raise


class _FakeTools:
    def status_text(self):
        return "🌐 web — buscar y leer páginas (todos)"


async def test_conexiones_is_owner_only(wired):
    app, _ = wired
    app.bot_data["tools"] = _FakeTools()
    cb = _callback(app, telegram.ext.CommandHandler, "conexiones")
    owner_replies, user_replies = [], []
    await cb(_update(42, "/conexiones", owner_replies), None)
    assert owner_replies == ["🌐 web — buscar y leer páginas (todos)"]
    await cb(_update(7, "/conexiones", user_replies), None)
    assert "código" in user_replies[0].lower() or "solo para el dueño" in user_replies[0]


async def test_send_checked_reports_success_and_failure():
    class Ok:
        def __init__(self):
            self.parts = []

        async def send_message(self, chat_id, text):
            self.parts.append(text)

    class Broken:
        async def send_message(self, chat_id, text):
            raise RuntimeError("Forbidden")

    ok = Ok()
    assert await main_mod._send_checked(ok, "7", "x" * 5000) is True
    assert [len(p) for p in ok.parts] == [4096, 904]
    assert await main_mod._send_checked(Broken(), "7", "hola") is False
