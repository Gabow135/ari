"""Wiring tests for L1: concurrent chat dispatch and per-user serialization."""
import asyncio
from types import SimpleNamespace

import telegram.ext

import ari.main as main_mod
from ari.application.access.gate import AccessGate
from ari.infrastructure.access.sqlite_access_store import SqliteAccessStore
from ari.infrastructure.persistence.db import connect


class _FakeApp:
    def __init__(self):
        self.handlers, self.bot_data = [], {}

        async def send_message(chat_id, text):
            pass

        self.bot = SimpleNamespace(send_message=send_message)

    def add_handler(self, h):
        self.handlers.append(h)

    def add_error_handler(self, _fn):
        pass

    def stop_running(self):
        pass

    def run_polling(self):
        pass


class _RecordingBuilder:
    last = None

    def __init__(self, app):
        self._app = app
        self.concurrent = None
        _RecordingBuilder.last = self

    def token(self, _t):
        return self

    def post_init(self, _f):
        return self

    def post_shutdown(self, _f):
        return self

    def concurrent_updates(self, n):
        self.concurrent = n
        return self

    def build(self):
        return self._app


def _run_main(monkeypatch, app, route=None):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("ARI_OWNER_IDS", "42")
    monkeypatch.setenv("ARI_LOG_FILE", "")  # console only: no ./logs in tests
    monkeypatch.setattr(telegram.ext.Application, "builder",
                        lambda: _RecordingBuilder(app))
    if route is not None:
        monkeypatch.setattr(main_mod, "route_message", route)
    main_mod.main()


def _callback(app):
    for h in app.handlers:
        if isinstance(h, telegram.ext.MessageHandler):
            return h.callback
    raise AssertionError("no message handler registered")


def _update(user_id, text):
    async def reply_text(_t):
        pass

    msg = SimpleNamespace(
        text=text, chat_id=user_id, reply_text=reply_text,
        from_user=SimpleNamespace(id=user_id, username="juan", first_name="Juan"))
    return SimpleNamespace(effective_message=msg, message=msg)


def test_main_enables_concurrent_updates_from_settings(monkeypatch):
    app = _FakeApp()
    _run_main(monkeypatch, app)
    assert _RecordingBuilder.last.concurrent == 8


def test_main_concurrent_updates_honors_env(monkeypatch):
    monkeypatch.setenv("ARI_MAX_CONCURRENT_CHATS", "12")
    app = _FakeApp()
    _run_main(monkeypatch, app)
    assert _RecordingBuilder.last.concurrent == 12


async def test_same_user_messages_are_serialized(monkeypatch):
    app = _FakeApp()
    started: list[str] = []
    first_started = asyncio.Event()
    release = asyncio.Event()

    async def route(text, user_id, deps):
        started.append(text)
        if text == "one":
            first_started.set()
        await release.wait()
        return "ok"

    _run_main(monkeypatch, app, route=route)
    conn = await connect(":memory:", embedding_dim=4)
    app.bot_data.update(
        gate=AccessGate(SqliteAccessStore(conn), owner_ids={"42"}),
        handler=None, confirm=None, confirm_command=None,
        coding_deps=main_mod.CodingDeps(None, None, None, None, None, None))
    on_message = _callback(app)

    # Both dispatched for the SAME user. ``release`` is set and both tasks are
    # drained in finally so neither path leaves a task pending (which would hang
    # teardown inside the ProgressMessage context).
    t1 = asyncio.ensure_future(on_message(_update(42, "one"), None))
    t2 = asyncio.ensure_future(on_message(_update(42, "two"), None))
    try:
        await asyncio.wait_for(first_started.wait(), timeout=1)
        await asyncio.sleep(0.05)  # let t2 reach the lock, if it's going to
        # The per-user lock keeps the second message waiting: only "one" ran.
        serialized = started == ["one"]
    finally:
        release.set()
        await asyncio.gather(t1, t2, return_exceptions=True)
    assert serialized, f"second message was not serialized: {started}"
    assert started == ["one", "two"]
    await conn.close()
