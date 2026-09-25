import asyncio
import dataclasses
import logging
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# Module-level set to keep references to background tasks so they cannot be
# garbage-collected while still running (Fix 4 — anti-GC guard).
_background_tasks: set = set()

from ari.application.access.gate import ADMIN_COMMANDS, AccessGate, deliver
from ari.application.admin.lifecycle import LIFECYCLE_COMMANDS, RESTART, Lifecycle
from ari.application.coding.authorizer import Authorizer
from ari.application.coding.confirm_coding import ConfirmCoding
from ari.application.coding.flow import CodingDeps, route_message
from ari.application.coding.pending_store import PendingStore
from ari.application.coding.request_coding import RequestCoding
from ari.application.handle_message import HandleMessage
from ari.application.memory_maintainer import MemoryMaintainer
from ari.application.schedule.heartbeat import Heartbeat
from ari.application.schedule.llm_health import MonitoredLLM
from ari.application.schedule.run_due_items import RunDueItems
from ari.application.schedule.schedule_actions import ScheduleActions
from ari.application.schedule.scheduler import Scheduler
from ari.application.schedule.system_notices import SystemNotices
from ari.config.settings import Settings
from ari.domain.agent.agent_service import AgentService
from ari.domain.ports.gateway_port import IncomingMessage
from ari.domain.schedule.quiet_hours import parse_window
from ari.infrastructure.access.sqlite_access_store import SqliteAccessStore
from ari.infrastructure.claude_env import claude_cli_env
from ari.infrastructure.coder.claude_code_coder import ClaudeCodeCoder
from ari.infrastructure.coder.workspace import Workspace
from ari.infrastructure.gateway.bot_commands import register_commands
from ari.infrastructure.gateway.progress_message import ProgressMessage
from ari.infrastructure.gateway.telegram_adapter import TelegramAdapter
from ari.infrastructure.llm.claude_code_adapter import ClaudeCodeCliAdapter
from ari.infrastructure.memory.embeddings import FastEmbedEmbeddings
from ari.infrastructure.memory.sqlite_memory_adapter import SqliteMemoryAdapter
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore
from ari.infrastructure.soul.soul_loader import SoulLoader
from ari.infrastructure.process import RESTART_NOTIFY_ENV, relaunch

logging.basicConfig(level=logging.INFO)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _send_quietly(bot, chat_id: str, text: str) -> None:
    """Background sends (reminders, notices, heartbeat): split to Telegram's
    limit and never raise — a failed send must not break the scheduler."""
    for part in TelegramAdapter.split_text(text):
        try:
            await bot.send_message(chat_id=int(chat_id), text=part)
        except Exception as exc:  # noqa: BLE001
            logging.warning("could not send to %s: %s", chat_id, exc)
            return


@dataclasses.dataclass
class Components:
    handler: HandleMessage
    conn: object
    memory: SqliteMemoryAdapter
    llm: MonitoredLLM
    schedule_store: SqliteScheduleStore
    actions: ScheduleActions
    agent: AgentService
    soul: SoulLoader


def cli_env(settings: Settings) -> dict | None:
    env = claude_cli_env(settings.claude_oauth_token, settings.claude_config_dir)
    if env is None:
        logging.warning("ARI_CLAUDE_OAUTH_TOKEN is not set: Ari runs the claude CLI with "
                        "the host login, so that account's email is visible to Ari. "
                        "Run `claude setup-token` and put the token in .env.")
    return env


async def build(settings: Settings, env: dict | None, tz) -> Components:
    embeddings = FastEmbedEmbeddings(settings.embedding_model)
    dim = len((await embeddings.embed(["probe"]))[0])
    conn = await connect(settings.db_path, embedding_dim=dim)
    memory = SqliteMemoryAdapter(conn, embedding_dim=dim)
    llm = MonitoredLLM(ClaudeCodeCliAdapter(model=settings.model,
                                            claude_bin=settings.claude_bin, cli_env=env))
    schedule_store = SqliteScheduleStore(conn)
    actions = ScheduleActions(schedule_store, tz, settings.max_items_per_user, clock=_utcnow)
    agent, soul = AgentService(), SoulLoader(settings.soul_dir)
    handler = HandleMessage(
        memory=memory, llm=llm, embeddings=embeddings, agent=agent,
        working_memory_size=settings.working_memory_size,
        recall_top_k=settings.recall_top_k,
        maintainer=MemoryMaintainer(memory, llm),
        soul=soul,
        is_owner=Authorizer(settings.owner_id_set).is_owner,
        actions=actions,
    )
    return Components(handler, conn, memory, llm, schedule_store, actions, agent, soul)


def main() -> None:
    settings = Settings()
    env = cli_env(settings)  # computed once: warns once when no token
    tz = ZoneInfo(settings.timezone)
    quiet = parse_window(settings.quiet_hours)
    lifecycle = Lifecycle(Authorizer(settings.owner_id_set).is_owner)
    # Set by a confirmed /stop or /restart; acted on once run_polling returns.
    exit_request: dict[str, str] = {}

    async def _post_init(app):
        c = await build(settings, env, tz)
        app.bot_data["handler"] = c.handler
        app.bot_data["conn"] = c.conn
        app.bot_data["actions"] = c.actions
        access_store = SqliteAccessStore(c.conn)
        app.bot_data["gate"] = AccessGate(access_store, settings.owner_id_set,
                                          on_revoke=c.schedule_store.cancel_user)
        if not settings.owner_id_set:
            logging.warning("ARI_OWNER_IDS is empty: nobody can approve access, "
                            "so every Telegram user will be blocked")
        await register_commands(app.bot, settings.owner_id_set)
        notify_chat = os.environ.pop(RESTART_NOTIFY_ENV, None)
        if notify_chat:
            try:
                await app.bot.send_message(chat_id=int(notify_chat),
                                           text="Listo, Ari está de vuelta.")
            except Exception as exc:  # noqa: BLE001 — best-effort notification
                logging.warning("could not send restart notice: %s", exc)

        # Coding infrastructure
        store = PendingStore()
        workspace = Workspace(settings.allowed_root)
        coder = ClaudeCodeCoder(
            model=settings.coder_model,
            claude_bin=settings.claude_bin,
            timeout=settings.coding_timeout_seconds,
            cli_env=env,
        )
        # default_dir: directory of this file's project root (the Ari repo itself)
        default_dir = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        request_coding = RequestCoding(coder, workspace, store, default_dir=default_dir)
        confirm = ConfirmCoding(coder, workspace, store)

        # Base deps: chat, confirm_coding, and scheduler are None/placeholder;
        # all three are bound per-message in _dispatch (scheduler uses the anti-GC wrapper).
        coding_deps = CodingDeps(
            authorizer=Authorizer(settings.owner_id_set),
            pending_store=store,
            request_coding=request_coding,
            confirm_coding=None,
            chat=None,
            scheduler=None,  # replaced per-message in _dispatch
        )
        app.bot_data["coding_deps"] = coding_deps
        app.bot_data["confirm"] = confirm

        # Proactivity: reminders/tasks, system notices, heartbeat.
        async def send(chat_id: str, text: str) -> None:
            await _send_quietly(app.bot, chat_id, text)

        notices = SystemNotices(c.schedule_store, access_store, settings.owner_id_set,
                                send, tz, quiet, _utcnow)
        c.llm.listener = notices

        async def run_task(item) -> str:
            out = await c.handler(IncomingMessage(item.user_id, item.chat_id, item.text),
                                  allow_actions=False)
            return out.text

        due = RunDueItems(c.schedule_store, send, run_task, notices.task_paused, tz, _utcnow)
        heartbeat = Heartbeat(
            llm=c.llm, memory=c.memory, store=c.schedule_store, agent=c.agent,
            soul=c.soul, checklist=SoulLoader(settings.soul_dir, "HEARTBEAT.md"),
            owners=settings.owner_id_set, send=send, tz=tz, quiet=quiet,
            interval_minutes=settings.heartbeat_minutes, clock=_utcnow)
        await c.schedule_store.reset_running()  # items interrupted by a crash/restart
        scheduler = Scheduler([due, notices.tick, heartbeat])
        scheduler.start()
        app.bot_data["scheduler"] = scheduler

    async def _post_shutdown(app):
        scheduler = app.bot_data.get("scheduler")
        if scheduler is not None:
            await scheduler.stop()
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

    async def _reply_parts(msg, text: str) -> None:
        for part in TelegramAdapter.split_text(text):
            await msg.reply_text(part)

    async def _send(chat_id: str, text: str) -> None:
        await app.bot.send_message(chat_id=int(chat_id), text=text)

    async def _admit(msg) -> bool:
        """Access gate: only owners and approved users get past this point."""
        user = msg.from_user
        result = await app.bot_data["gate"].check(str(user.id), user.username)
        await deliver(result, lambda t: _reply_parts(msg, t), _send)
        return result.allowed

    async def _dispatch(update, text: str) -> None:
        """Shared dispatch: builds per-message deps and routes the text."""
        msg = update.effective_message
        if msg is None or msg.from_user is None:
            return
        if not await _admit(msg):
            return
        user_id = str(msg.from_user.id)

        # A reply to a pending /stop or /restart is consumed here.
        confirmation = lifecycle.confirm(text, user_id)
        if confirmation.handled:
            await _reply_parts(msg, confirmation.reply)
            if confirmation.action is not None:
                exit_request.update(action=confirmation.action, chat=user_id)
                app.stop_running()
            return

        message_handler = app.bot_data["handler"]
        base_deps = app.bot_data["coding_deps"]
        confirm = app.bot_data["confirm"]

        async def report(reply_text: str) -> None:
            for part in TelegramAdapter.split_text(reply_text):
                await msg.reply_text(part)

        async def chat(chat_text: str, uid: str) -> str:
            inc = TelegramAdapter.to_incoming(update)
            if inc is None:
                return ""
            # Build a fresh IncomingMessage reflecting the actual text for this call.
            scoped_inc = IncomingMessage(
                user_id=inc.user_id, chat_id=inc.chat_id, text=chat_text)
            out = await message_handler(scoped_inc)
            return out.text

        def _schedule(coro):
            task = asyncio.ensure_future(coro)
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)

        async def _confirm_with_progress(uid, action) -> None:
            # Background coding run gets its own progress message (the one of the
            # "dale" turn is already gone by then).
            async with ProgressMessage(app.bot, msg.chat_id):
                await confirm(uid, action, report)

        # Per-message deps: never mutate the shared base instance.
        local_deps = dataclasses.replace(
            base_deps,
            chat=chat,
            confirm_coding=_confirm_with_progress,
            scheduler=_schedule,
        )

        # "typing…" + a live progress message, deleted before the final reply.
        async with ProgressMessage(app.bot, msg.chat_id):
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

    async def _on_start(update, _context) -> None:
        """/start: greet approved users; hand a pairing code to everyone else."""
        msg = update.effective_message
        if msg is None or msg.from_user is None:
            return
        if await _admit(msg):
            await msg.reply_text("¡Hola! Escríbeme cuando quieras.")

    async def _on_reminders(update, _context) -> None:
        """/recordatorios: the sender's active reminders and tasks."""
        msg = update.effective_message
        if msg is None or msg.from_user is None:
            return
        if await _admit(msg):
            await _reply_parts(msg, await app.bot_data["actions"].list_text(
                str(msg.from_user.id)))

    async def _on_access_admin(update, _context) -> None:
        """Owner-only /aprobar, /revocar, /accesos."""
        msg = update.effective_message
        if msg is None or msg.from_user is None:
            return
        result = await app.bot_data["gate"].admin_command(msg.text, str(msg.from_user.id))
        await deliver(result, lambda t: _reply_parts(msg, t), _send)

    async def _on_lifecycle(update, _context) -> None:
        """Owner-only /stop and /restart; the next message confirms or cancels."""
        msg = update.effective_message
        if msg is None or msg.from_user is None:
            return
        action = msg.text.strip().split()[0].lstrip("/").split("@")[0].lower()
        await _reply_parts(msg, lifecycle.request(action, str(msg.from_user.id)))

    # Slash commands reach _on_command; plain text reaches _on_message.
    app.add_handler(CommandHandler("start", _on_start))
    app.add_handler(CommandHandler("recordatorios", _on_reminders))
    app.add_handler(CommandHandler(list(ADMIN_COMMANDS), _on_access_admin))
    app.add_handler(CommandHandler(list(LIFECYCLE_COMMANDS), _on_lifecycle))
    app.add_handler(CommandHandler(["code", "fase2"], _on_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_message))
    app.run_polling()

    # run_polling returned: shutdown (incl. closing the DB) is complete.
    if exit_request.get("action") == RESTART:
        logging.info("restarting Ari")
        relaunch(exit_request["chat"])


if __name__ == "__main__":
    main()
