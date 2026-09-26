import logging

from ari.domain.ports.gateway_port import IncomingMessage

log = logging.getLogger("ari.telegram")
TELEGRAM_LIMIT = 4096


# Phase 1: the composition root (main.py) owns the PTB run_polling loop and
# wires the on-message handler via post_init so the aiosqlite connection is
# always created inside the same event loop that run_polling drives.
# TelegramAdapter exposes only the stateless conversion helpers used there.
class TelegramAdapter:
    @staticmethod
    def to_incoming(update) -> IncomingMessage | None:
        msg = getattr(update, "effective_message", None) or getattr(update, "message", None)
        if msg is None or not getattr(msg, "text", None):
            return None
        user = msg.from_user
        return IncomingMessage(
            user_id=str(user.id), chat_id=str(msg.chat_id), text=msg.text,
            display_name=getattr(user, "first_name", None) or getattr(user, "username", None) or "")

    @staticmethod
    def split_text(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
        return [text[i:i + limit] for i in range(0, len(text), limit)] or [""]
