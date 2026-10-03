"""The bot error handler must end this instance on a Telegram Conflict
(another process is already polling the same bot token) instead of looping
forever over getUpdates, and must log — never silently swallow — any other
error that reaches it."""
import asyncio
import types

import telegram.ext
from telegram.error import Conflict, NetworkError

import ari.main as main_mod
from ari.infrastructure.bot_errors import handle_bot_error


def test_conflict_stops_this_instance():
    stopped = []
    handled = handle_bot_error(
        Conflict("terminated by other getUpdates request"),
        stop=lambda: stopped.append(True),
    )
    assert handled is True
    assert stopped == [True]  # the losing instance shuts itself down


def test_other_errors_are_logged_but_do_not_stop():
    stopped = []
    handled = handle_bot_error(NetworkError("transient"), stop=lambda: stopped.append(True))
    assert handled is False
    assert stopped == []  # a transient error must never kill the bot


class _FakeApp:
    def __init__(self):
        self.bot_data = {}
        self.error_handler = None
        self.stopped = 0

    def add_handler(self, _h):
        pass

    def add_error_handler(self, fn):
        self.error_handler = fn

    def stop_running(self):
        self.stopped += 1

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


def test_main_registers_the_conflict_handler(monkeypatch):
    app = _FakeApp()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("ARI_LOG_FILE", "")  # console only: main() must not write ./logs in tests
    monkeypatch.setattr(telegram.ext.Application, "builder", lambda: _FakeBuilder(app))

    main_mod.main()

    assert app.error_handler is not None, "main() must register an error handler"
    ctx = types.SimpleNamespace(error=Conflict("x"), application=app)
    asyncio.run(app.error_handler(None, ctx))
    assert app.stopped == 1  # a Conflict reaching the handler stops this instance
