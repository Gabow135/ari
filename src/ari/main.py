import asyncio
import logging

from ari.application.handle_message import HandleMessage
from ari.application.memory_maintainer import MemoryMaintainer
from ari.config.settings import Settings
from ari.domain.agent.agent_service import AgentService
from ari.infrastructure.gateway.telegram_adapter import TelegramAdapter
from ari.infrastructure.llm.claude_code_adapter import ClaudeCodeCliAdapter
from ari.infrastructure.memory.embeddings import FastEmbedEmbeddings
from ari.infrastructure.memory.sqlite_memory_adapter import SqliteMemoryAdapter
from ari.infrastructure.persistence.db import connect

logging.basicConfig(level=logging.INFO)


async def build(settings: Settings):
    embeddings = FastEmbedEmbeddings(settings.embedding_model)
    dim = len((await embeddings.embed(["probe"]))[0])
    conn = await connect(settings.db_path, embedding_dim=dim)
    memory = SqliteMemoryAdapter(conn, embedding_dim=dim)
    llm = ClaudeCodeCliAdapter(model=settings.model, claude_bin=settings.claude_bin)
    handler = HandleMessage(
        memory=memory, llm=llm, embeddings=embeddings, agent=AgentService(),
        working_memory_size=settings.working_memory_size,
        recall_top_k=settings.recall_top_k,
        maintainer=MemoryMaintainer(memory, llm),
    )
    return handler, conn


def main() -> None:
    settings = Settings()

    async def _post_init(app):
        handler, conn = await build(settings)
        app.bot_data["handler"] = handler
        app.bot_data["conn"] = conn

    async def _post_shutdown(app):
        conn = app.bot_data.get("conn")
        if conn is not None:
            await conn.close()

    from telegram.ext import Application, MessageHandler, filters

    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    async def _on_message(update, _context):
        handler = app.bot_data["handler"]
        incoming = TelegramAdapter.to_incoming(update)
        if incoming is None:
            return
        out = await handler(incoming)
        for part in TelegramAdapter.split_text(out.text):
            await update.effective_message.reply_text(part)

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_message))
    app.run_polling()


if __name__ == "__main__":
    main()
