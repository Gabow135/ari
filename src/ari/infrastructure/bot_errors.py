"""Central handling for errors that surface from the Telegram polling loop.

The important case is :class:`telegram.error.Conflict` (HTTP 409): Telegram
allows only one ``getUpdates`` reader per bot token, so when a second Ari
instance starts it terminates the older one with a Conflict. Without an error
handler python-telegram-bot just logs the traceback and both instances keep
fighting forever, so neither answers. Here we stop the losing instance instead,
leaving exactly one Ari alive and working.
"""
import logging

from telegram.error import Conflict

log = logging.getLogger("ari.bot")

CONFLICT_MESSAGE = (
    "Telegram Conflict (409): another Ari instance is already polling this bot "
    "token. Only one may run at a time — stopping this instance so the other "
    "keeps working. Run just one process, or use /restart in the chat."
)


def handle_bot_error(error: Exception, *, stop) -> bool:
    """Handle an error raised from the polling loop or a handler.

    On a :class:`Conflict` (another process owns the token), log a clear
    message, call ``stop`` to shut this instance down, and return ``True``.
    Any other error is logged with its traceback and left untouched, returning
    ``False`` — a transient error must never take the bot down.
    """
    if isinstance(error, Conflict):
        log.error(CONFLICT_MESSAGE)
        stop()
        return True
    log.error("unhandled error in the Telegram polling loop: %s", error, exc_info=error)
    return False
