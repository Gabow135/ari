import logging

from telegram import Update
from telegram.ext import Application, MessageHandler, filters

from ari.domain.ports.gateway_port import Handler, IncomingMessage

log = logging.getLogger("ari.telegram")
TELEGRAM_LIMIT = 4096


class TelegramAdapter:
    def __init__(self, token: str):
        self._app = Application.builder().token(token).build()

    @staticmethod
    def to_incoming(update) -> IncomingMessage | None:
        msg = getattr(update, "effective_message", None) or getattr(update, "message", None)
        if msg is None or not getattr(msg, "text", None):
            return None
        return IncomingMessage(
            user_id=str(msg.from_user.id), chat_id=str(msg.chat_id), text=msg.text)

    @staticmethod
    def split_text(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
        return [text[i:i + limit] for i in range(0, len(text), limit)] or [""]

    async def start(self, handler: Handler) -> None:
        async def _on_message(update: Update, _context) -> None:
            incoming = self.to_incoming(update)
            if incoming is None:
                log.info("skipping non-text update")
                return
            out = await handler(incoming)
            for part in self.split_text(out.text):
                await update.effective_message.reply_text(part)

        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_message))
        self._app.run_polling()
