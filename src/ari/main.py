import asyncio
import dataclasses
import logging
import os

from ari.application.coding.authorizer import Authorizer
from ari.application.coding.confirm_coding import ConfirmCoding
from ari.application.coding.flow import CodingDeps, route_message
from ari.application.coding.pending_store import PendingStore
from ari.application.coding.request_coding import RequestCoding
from ari.application.handle_message import HandleMessage
from ari.application.memory_maintainer import MemoryMaintainer
from ari.config.settings import Settings
from ari.domain.agent.agent_service import AgentService
from ari.infrastructure.coder.claude_code_coder import ClaudeCodeCoder
from ari.infrastructure.coder.workspace import Workspace
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

        # Coding infrastructure
        store = PendingStore()
        workspace = Workspace(settings.allowed_root)
        coder = ClaudeCodeCoder(
            model=settings.coder_model,
            claude_bin=settings.claude_bin,
            timeout=settings.coding_timeout_seconds,
        )
        # default_dir: directory of this file's project root (the Ari repo itself)
        default_dir = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        request_coding = RequestCoding(coder, workspace, store, default_dir=default_dir)
        confirm = ConfirmCoding(coder, workspace, store)

        # Base deps: chat and confirm_coding are None; bound per-message in _dispatch.
        coding_deps = CodingDeps(
            authorizer=Authorizer(settings.owner_id_set),
            pending_store=store,
            request_coding=request_coding,
            confirm_coding=None,
            chat=None,
            scheduler=lambda coro: asyncio.ensure_future(coro),
        )
        app.bot_data["coding_deps"] = coding_deps
        app.bot_data["confirm"] = confirm

    async def _post_shutdown(app):
        conn = app.bot_data.get("conn")
        if conn is not None:
            await conn.close()

    from telegram.ext import Application, CommandHandler, MessageHandler, filters

    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    async def _dispatch(update, text: str) -> None:
        """Shared dispatch: builds per-message deps and routes the text."""
        msg = update.effective_message
        if msg is None:
            return

        message_handler = app.bot_data["handler"]
        base_deps = app.bot_data["coding_deps"]
        confirm = app.bot_data["confirm"]
        user_id = str(msg.from_user.id)

        async def report(reply_text: str) -> None:
            for part in TelegramAdapter.split_text(reply_text):
                await msg.reply_text(part)

        async def chat(chat_text: str, uid: str) -> str:
            inc = TelegramAdapter.to_incoming(update)
            if inc is None:
                return ""
            # Build a fresh IncomingMessage reflecting the actual text for this call.
            from ari.domain.ports.gateway_port import IncomingMessage
            scoped_inc = IncomingMessage(
                user_id=inc.user_id, chat_id=inc.chat_id, text=chat_text)
            out = await message_handler(scoped_inc)
            return out.text

        # Per-message deps: never mutate the shared base instance.
        local_deps = dataclasses.replace(
            base_deps,
            chat=chat,
            confirm_coding=lambda uid: confirm(uid, report),
        )

        reply = await route_message(text, user_id, local_deps)
        if reply is not None:
            for part in TelegramAdapter.split_text(reply):
                await msg.reply_text(part)

    async def _on_command(update, _context) -> None:
        """Handle /code and /fase2 slash commands."""
        msg = update.effective_message
        if msg is None:
            return
        # msg.text is the full original text, e.g. "/code dir:. add X"
        await _dispatch(update, msg.text)

    async def _on_message(update, _context) -> None:
        """Handle plain-text messages (chat, dale/no confirmations)."""
        inc = TelegramAdapter.to_incoming(update)
        if inc is None:
            return
        await _dispatch(update, inc.text)

    # Slash commands reach _on_command; plain text reaches _on_message.
    app.add_handler(CommandHandler(["code", "fase2"], _on_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_message))
    app.run_polling()


if __name__ == "__main__":
    main()
