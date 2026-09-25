"""Wiring test: shutdown must drain in-flight scheduled tasks (RunDueItems)
before closing the DB connection, and never let a slow drain hang shutdown
forever or raise out of _post_shutdown."""
import asyncio

import pytest
import telegram.ext

import ari.main as main_mod


class _FakeApp:
    def __init__(self):
        self.bot_data = {}
        self.post_init_fn = None
        self.post_shutdown_fn = None
        self.script = None

    def add_handler(self, _h):
        pass

    def stop_running(self):
        pass

    def run_polling(self):
        if self.script is not None:
            asyncio.run(self.script(self))


class _FakeBuilder:
    def __init__(self, app):
        self._app = app

    def token(self, _t):
        return self

    def post_init(self, f):
        self._app.post_init_fn = f
        return self

    def post_shutdown(self, f):
        self._app.post_shutdown_fn = f
        return self

    def build(self):
        return self._app


class _FakeScheduler:
    def __init__(self):
        self.stopped = False

    async def stop(self):
        self.stopped = True


class _FakeConn:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class _FakeDue:
    def __init__(self, hang=False):
        self.drained = False
        self._hang = hang

    async def drain(self):
        if self._hang:
            await asyncio.sleep(10)
        self.drained = True


def _wire(monkeypatch):
    app = _FakeApp()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setattr(telegram.ext.Application, "builder", lambda: _FakeBuilder(app))
    return app


def test_shutdown_drains_due_before_closing_db(monkeypatch):
    app = _wire(monkeypatch)
    scheduler, conn, due = _FakeScheduler(), _FakeConn(), _FakeDue()
    order = []

    async def tracking_stop():
        scheduler.stopped = True
        order.append("scheduler")

    async def tracking_drain():
        due.drained = True
        order.append("drain")

    async def tracking_close():
        conn.closed = True
        order.append("close")

    scheduler.stop, due.drain, conn.close = tracking_stop, tracking_drain, tracking_close

    async def script(a):
        a.bot_data.update(scheduler=scheduler, conn=conn, due=due)
        await a.post_shutdown_fn(a)

    app.script = script
    main_mod.main()
    assert scheduler.stopped and due.drained and conn.closed
    assert order == ["scheduler", "drain", "close"]


def test_shutdown_survives_a_drain_that_never_finishes(monkeypatch):
    app = _wire(monkeypatch)
    monkeypatch.setattr(main_mod, "_DRAIN_TIMEOUT", 0.05, raising=False)
    scheduler, conn, due = _FakeScheduler(), _FakeConn(), _FakeDue(hang=True)

    async def script(a):
        a.bot_data.update(scheduler=scheduler, conn=conn, due=due)
        await a.post_shutdown_fn(a)  # must not raise / must not hang

    app.script = script
    main_mod.main()  # would hang ~10s or raise TimeoutError without the fix
    assert scheduler.stopped and conn.closed  # shutdown proceeds regardless
    assert not due.drained  # the hanging drain never actually completed
