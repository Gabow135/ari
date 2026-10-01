import asyncio
import dataclasses
import logging
import os
import sys
import os.path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# Module-level set to keep references to background tasks so they cannot be
# garbage-collected while still running (Fix 4 — anti-GC guard).
_background_tasks: set = set()

from ari.application.access.gate import ADMIN_COMMANDS, AccessGate, deliver
from ari.application.admin.lifecycle import LIFECYCLE_COMMANDS, RESTART, Lifecycle
from ari.application.skills.skill_admin import SkillAdmin
from ari.application.skills.skill_manager import SkillManager
from ari.application.coding.authorizer import Authorizer
from ari.application.coding.confirm_coding import ConfirmCoding
from ari.application.coding.flow import CodingDeps, route_message
from ari.application.coding.pending_store import PendingStore
from ari.application.coding.request_coding import RequestCoding
from ari.application.coding.request_runner import CodingRequestRunner
from ari.application.command.confirm_command import ConfirmCommand
from ari.application.command.request_runner import CommandRequestRunner
from ari.application.credentials.request_runner import CredentialRequestRunner
from ari.application.credentials.needs import missing_inbound_secrets
from ari.application.missions.run_pending_missions import MissionRunner
from ari.application.handle_message import HandleMessage
from ari.domain.memory.recall_ranker import RankWeights, RecallRanker
from ari.application.memory_maintainer import MemoryMaintainer
from ari.application.outbox import OutboxFlusher
from ari.application.schedule.heartbeat import Heartbeat
from ari.application.schedule.llm_health import MonitoredLLM
from ari.application.schedule.run_due_items import RunDueItems
from ari.application.schedule.schedule_actions import ScheduleActions
from ari.application.schedule.scheduler import Scheduler
from ari.application.schedule.system_notices import SystemNotices
from ari.application.text_format import truncate
from ari.application.tools.tool_policy import AriServerSpec, ToolPolicy
from ari.config.settings import Settings
from ari.domain.agent.agent_service import AgentService
from ari.domain.ports.gateway_port import IncomingMessage, OutgoingMessage
from ari.domain.skills.models import InboundContext
from ari.domain.schedule.quiet_hours import parse_window
from ari.infrastructure.access.sqlite_access_store import SqliteAccessStore
from ari.infrastructure.claude_env import claude_cli_env
from ari.infrastructure.coder.claude_code_coder import ClaudeCodeCoder
from ari.infrastructure.coder.verifier import CoderVerifier
from ari.infrastructure.coder.workspace import Workspace
from ari.infrastructure.command.shell_runner import ShellRunner
from ari.infrastructure.gateway.bot_commands import register_commands
from ari.infrastructure.gateway.progress_message import ProgressMessage
from ari.infrastructure.gateway.telegram_adapter import TelegramAdapter, voice_to_text, media_to_text
from ari.infrastructure.llm.claude_code_adapter import ClaudeCodeCliAdapter
from ari.infrastructure.memory.embeddings import FastEmbedEmbeddings
from ari.infrastructure.memory.sqlite_memory_adapter import SqliteMemoryAdapter
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_coding_requests import SqliteCodingRequests
from ari.infrastructure.persistence.sqlite_command_requests import SqliteCommandRequests
from ari.infrastructure.persistence.sqlite_credential_requests import SqliteCredentialRequests
from ari.infrastructure.persistence.sqlite_missions import SqliteMissions
from ari.infrastructure.persistence.sqlite_turn_log import SqliteTurnLog
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore
from ari.infrastructure.soul.soul_loader import SoulLoader
from ari.infrastructure.tools.mcp_registry import McpRegistry
from ari.infrastructure.tools.turn_config import TurnConfigWriter
from ari.infrastructure.vault.fernet_vault import FernetVault
from ari.infrastructure.vault_web.maintainer import VaultWebMaintainer
from ari.infrastructure.bot_errors import handle_bot_error
from ari.infrastructure.process import RESTART_NOTIFY_ENV, relaunch

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ari.main")

# How long shutdown waits for in-flight scheduled tasks (RunDueItems background
# tasks) to finish before giving up and closing the DB connection anyway.
_DRAIN_TIMEOUT = 30


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _send_checked(bot, chat_id: str, text: str) -> bool:
    """Send split to Telegram's limit; True if every part went out. Never raises."""
    for part in TelegramAdapter.split_text(text):
        try:
            await bot.send_message(chat_id=int(chat_id), text=part)
        except Exception as exc:  # noqa: BLE001
            logging.warning("could not send to %s: %s", chat_id, exc)
            return False
    return True


async def _send_quietly(bot, chat_id: str, text: str) -> None:
    """Background sends (reminders, notices, heartbeat): best effort, never raise."""
    await _send_checked(bot, chat_id, text)


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
    tools: ToolPolicy
    turn_log: SqliteTurnLog
    turn_config_writer: TurnConfigWriter
    vault_web: VaultWebMaintainer
    skills: "SkillManager"


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
    vault = FernetVault(settings.vault_path, settings.vault_key)
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sensitive = (project_root, settings.vault_path,
                 os.path.join(project_root, ".env"), os.path.abspath(settings.claude_config_dir))
    registry = McpRegistry(settings.mcp_config, ".env",
                           os.path.join(settings.claude_config_dir, "mcp"),
                           vault=vault, sensitive_paths=sensitive)
    turn_log = SqliteTurnLog(conn)
    ari_spec = AriServerSpec(sys.executable, ("-m", "ari.mcp_server"), {
        "ARI_DB_PATH": os.path.abspath(settings.db_path),
        "ARI_TIMEZONE": settings.timezone,
        "ARI_MAX_ITEMS": str(settings.max_items_per_user),
        "ARI_OWNER_IDS": ",".join(sorted(settings.owner_id_set)),
        "ARI_SKILLS_DIR": os.path.abspath(settings.skills_dir),
    })
    turn_config_writer = TurnConfigWriter(os.path.join(settings.claude_config_dir, "mcp"))
    tools = ToolPolicy(registry, Authorizer(settings.owner_id_set).is_owner, ari=ari_spec,
                       writer=turn_config_writer)
    llm = MonitoredLLM(ClaudeCodeCliAdapter(model=settings.model,
                                            claude_bin=settings.claude_bin, cli_env=env,
                                            timeout=settings.chat_timeout_seconds))
    schedule_store = SqliteScheduleStore(conn)
    actions = ScheduleActions(schedule_store, tz, settings.max_items_per_user, clock=_utcnow)
    agent, soul = AgentService(), SoulLoader(settings.soul_dir)
    ranker = RecallRanker(RankWeights(
        similarity=settings.rank_similarity_weight,
        recency=settings.rank_recency_weight,
        importance=settings.rank_importance_weight,
        recency_half_life_days=settings.rank_recency_half_life_days,
        min_similarity=settings.rank_min_similarity,
    ))
    handler = HandleMessage(
        memory=memory, llm=llm, embeddings=embeddings, agent=agent,
        working_memory_size=settings.working_memory_size,
        recall_top_k=settings.recall_top_k,
        maintainer=MemoryMaintainer(memory, llm),
        soul=soul,
        is_owner=Authorizer(settings.owner_id_set).is_owner,
        actions=actions,
        tools=tools,
        turn_log=turn_log,
        ranker=ranker,
        candidate_multiplier=settings.candidate_multiplier,
        dedup_similarity=settings.dedup_similarity,
    )
    cert_dir = os.path.dirname(os.path.expanduser(settings.vault_path))
    skills = SkillManager(settings.skills_dir, vault)
    vault_web = VaultWebMaintainer(
        vault, settings.mcp_config, cert_dir=cert_dir, port=settings.vault_web_port,
        bind=settings.vault_web_bind, ttl_minutes=settings.vault_web_ttl_minutes,
        extra_names=skills.required_secret_names)
    return Components(handler, conn, memory, llm, schedule_store, actions, agent, soul, tools,
                      turn_log, turn_config_writer, vault_web, skills)


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
        app.bot_data["tools"] = c.tools
        app.bot_data["vault_web"] = c.vault_web
        app.bot_data["skills"] = c.skills
        app.bot_data["skill_admin"] = SkillAdmin(c.skills, c.vault_web)
        for line in c.tools.status_text().splitlines():
            logging.info("conexión: %s", line)
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
        # Verify on the ari/tg branch, then auto-merge into the live branch only on
        # green (user-chosen strategy). on_merged reloads skills so the running bot
        # picks up a new/edited skill without a restart and reports what went live.
        verifier = CoderVerifier(timeout=settings.coding_timeout_seconds)

        async def _on_coding_merged() -> list[str]:
            return c.skills.reload()

        confirm = ConfirmCoding(coder, workspace, store,
                                verifier=verifier, on_merged=_on_coding_merged)

        # Terminal infrastructure: proponer_comando → «dale» → run shell on the host.
        # A separate pending slot so commands never touch the coding state machine.
        command_store = PendingStore()
        confirm_command = ConfirmCommand(ShellRunner(), command_store, cwd=default_dir)

        # Base deps: chat, confirm_coding, confirm_command, and scheduler are
        # None/placeholder; all are bound per-message in _dispatch (scheduler uses
        # the anti-GC wrapper).
        coding_deps = CodingDeps(
            authorizer=Authorizer(settings.owner_id_set),
            pending_store=store,
            request_coding=request_coding,
            confirm_coding=None,
            chat=None,
            scheduler=None,  # replaced per-message in _dispatch
            command_store=command_store,
            confirm_command=None,  # replaced per-message in _dispatch
        )
        app.bot_data["coding_deps"] = coding_deps
        app.bot_data["confirm"] = confirm
        app.bot_data["confirm_command"] = confirm_command

        # Proactivity: reminders/tasks, system notices, heartbeat.
        async def send(chat_id: str, text: str) -> None:
            await _send_quietly(app.bot, chat_id, text)

        async def send_checked(chat_id: str, text: str) -> bool:
            return await _send_checked(app.bot, chat_id, text)

        flusher = OutboxFlusher(c.turn_log, send_checked, _utcnow)

        def spawn(coro) -> None:
            task = asyncio.ensure_future(coro)
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)

        coding_requests = SqliteCodingRequests(c.conn)
        coding_runner = CodingRequestRunner(
            coding_requests, request_coding, store, send, spawn, _utcnow,
            progress=lambda chat_id: ProgressMessage(app.bot, int(chat_id)))
        command_requests = SqliteCommandRequests(c.conn)
        command_runner = CommandRequestRunner(command_requests, command_store, send, _utcnow)
        credential_requests = SqliteCredentialRequests(c.conn)
        credential_runner = CredentialRequestRunner(credential_requests, c.vault_web, send, _utcnow)

        async def after_turn() -> None:
            # Each step is guarded on its own so a failure in one (e.g. the
            # outbox flush) never skips the other (e.g. a queued code proposal).
            try:
                await flusher()
            except Exception:
                log.exception("outbox flush failed")
            try:
                await coding_runner()
            except Exception:
                log.exception("coding request runner failed")
            try:
                await command_runner()
            except Exception:
                log.exception("command request runner failed")
            try:
                await credential_runner()
            except Exception:
                log.exception("credential request runner failed")

        c.handler._after_turn = after_turn  # flush + proposed code, right after each turn

        notices = SystemNotices(c.schedule_store, access_store, settings.owner_id_set,
                                send, tz, quiet, _utcnow)
        c.llm.listener = notices

        async def run_task(item) -> str:
            out = await c.handler(IncomingMessage(item.user_id, item.chat_id, item.text),
                                  allow_actions=False)
            return out.text

        due = RunDueItems(c.schedule_store, send, run_task, notices.task_paused, tz, _utcnow)
        app.bot_data["due"] = due

        # Orphaned per-turn MCP configs (e.g. a crash between write and the
        # `finally` that removes them) never carry secrets beyond the DB path,
        # but they do carry actor identity: swept away before anything else runs.
        swept = c.turn_config_writer.sweep()
        if swept:
            logging.info("swept %d orphaned turn config file(s)", swept)

        heartbeat = Heartbeat(
            llm=c.llm, memory=c.memory, store=c.schedule_store, agent=c.agent,
            soul=c.soul, checklist=SoulLoader(settings.soul_dir, "HEARTBEAT.md"),
            owners=settings.owner_id_set, send=send, tz=tz, quiet=quiet,
            interval_minutes=settings.heartbeat_minutes, clock=_utcnow, tools=c.tools)
        await c.schedule_store.reset_running()  # items interrupted by a crash/restart
        for stranded in await coding_requests.reset_taken():  # plans interrupted likewise
            await send(stranded.chat_id,
                       f"Se interrumpió la preparación del plan: {truncate(stranded.instruction)}")
        for stranded in await command_requests.reset_taken():  # commands interrupted before «dale»
            await send(stranded.chat_id,
                       f"Se interrumpió un comando pendiente: {truncate(stranded.command)}")
        for r in await credential_requests.reset_taken():
            await send(r.chat_id, "Retomo tu pedido de credencial…")

        missions = SqliteMissions(c.conn)
        stranded_missions = await missions.reset_running()
        if stranded_missions:
            log.info("reset %d stranded mission(s) to pending", len(stranded_missions))
        mission_runner = MissionRunner(missions, c.handler, send, spawn)

        async def vault_web_sweep() -> None:
            c.vault_web.sweep_and_maybe_stop()

        scheduler = Scheduler([due, notices.tick, heartbeat, flusher, coding_runner,
                               command_runner, mission_runner, credential_runner,
                               vault_web_sweep])
        scheduler.start()
        app.bot_data["scheduler"] = scheduler

    async def _post_shutdown(app):
        scheduler = app.bot_data.get("scheduler")
        if scheduler is not None:
            await scheduler.stop()
        due = app.bot_data.get("due")
        if due is not None:
            try:
                await asyncio.wait_for(due.drain(), timeout=_DRAIN_TIMEOUT)
            except asyncio.TimeoutError:
                logging.warning(
                    "timed out after %ss draining in-flight scheduled tasks; "
                    "closing the DB anyway", _DRAIN_TIMEOUT)
        vw = app.bot_data.get("vault_web")
        if vw is not None:
            vw.stop()
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

    async def _dispatch(update, text: str, from_voice: bool = False) -> None:
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
        confirm_command = app.bot_data["confirm_command"]

        async def report(reply_text: str) -> None:
            for part in TelegramAdapter.split_text(reply_text):
                await msg.reply_text(part)

        async def chat(chat_text: str, uid: str) -> str:
            # Don't use TelegramAdapter.to_incoming here — it returns None for
            # voice/audio updates (msg.text is None). Build IncomingMessage directly
            # from msg so voice transcripts are dispatched correctly too.
            if msg is None or msg.from_user is None:
                return ""
            user = msg.from_user
            scoped_inc = IncomingMessage(
                user_id=str(user.id), chat_id=str(msg.chat_id), text=chat_text,
                display_name=getattr(user, "first_name", None) or getattr(user, "username", None) or "")
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

        async def _confirm_command_with_progress(uid, action) -> None:
            # Same shape as the coding confirm: its own progress message while the
            # shell command runs, then the output is reported.
            async with ProgressMessage(app.bot, msg.chat_id):
                await confirm_command(uid, action, report)

        # Per-message deps: never mutate the shared base instance.
        local_deps = dataclasses.replace(
            base_deps,
            chat=chat,
            confirm_coding=_confirm_with_progress,
            confirm_command=_confirm_command_with_progress,
            scheduler=_schedule,
        )

        # "typing…" + a live progress message, deleted before the final reply.
        async with ProgressMessage(app.bot, msg.chat_id):
            reply = await route_message(text, user_id, local_deps)
        if reply is not None:
            for part in TelegramAdapter.split_text(reply):
                await msg.reply_text(part)
            if from_voice:
                origin = InboundContext(came_from_voice=True, chat_id=str(msg.chat_id), user_id=user_id)
                is_owner = app.bot_data["gate"].is_owner(user_id)
                deliveries = await app.bot_data["skills"].run_outbound(
                    OutgoingMessage(chat_id=str(msg.chat_id), text=reply), origin, is_owner)
                for d in deliveries:
                    if d.kind == "voice" and d.data:
                        try:
                            await app.bot.send_voice(chat_id=int(msg.chat_id), voice=bytes(d.data))
                        except Exception:
                            try:
                                await app.bot.send_audio(chat_id=int(msg.chat_id), audio=bytes(d.data))
                            except Exception as exc:
                                logging.warning("voice reply failed for %s: %s", msg.chat_id, exc)

    async def _on_command(update, _context) -> None:
        """Handle /code and /fase2 slash commands."""
        msg = update.effective_message
        if msg is None:
            return
        # msg.text is the full original text, e.g. "/code dir:. add X"
        await _dispatch(update, msg.text)

    async def _on_message(update, _context) -> None:
        """Handle plain-text messages (chat, dale/no confirmations) and voice notes."""
        inc = TelegramAdapter.to_incoming(update)
        if inc is not None:
            await _dispatch(update, inc.text)
            return
        msg = update.effective_message
        if msg is None or msg.from_user is None:
            return
        if getattr(msg, "photo", None) or getattr(msg, "document", None):
            if not await _admit(msg):
                return
            is_owner = app.bot_data["gate"].is_owner(str(msg.from_user.id))

            async def _download(media):
                f = await media.get_file()
                return await f.download_as_bytearray()

            try:
                text = await media_to_text(update, _download, app.bot_data["skills"], is_owner)
            except Exception:
                log.exception("media_to_text raised for user %s", msg.from_user.id)
                await msg.reply_text("Hubo un error procesando el archivo. ¿Lo resumes por texto?")
                return
            if not text:
                missing = missing_inbound_secrets(app.bot_data["skills"].list())
                if is_owner and missing:
                    try:
                        link = app.bot_data["vault_web"].new_link()
                        await msg.reply_text(
                            f"Necesito {', '.join(missing)} para leer eso. Cárgala en la misma "
                            f"red (vence pronto):\n{link}")
                    except Exception:
                        await msg.reply_text("No pude procesar ese archivo. ¿Lo resumes por texto?")
                else:
                    await msg.reply_text(
                        "No puedo leer ese tipo de archivo por ahora. ¿Me lo resumes por texto?")
                return
            await _dispatch(update, text)
            return
        if not (getattr(msg, "voice", None) or getattr(msg, "audio", None)):
            return
        if not await _admit(msg):
            return
        is_owner = app.bot_data["gate"].is_owner(str(msg.from_user.id))

        async def _download(media):
            f = await media.get_file()
            return await f.download_as_bytearray()

        try:
            text = await voice_to_text(update, _download, app.bot_data["skills"], is_owner)
        except Exception:
            log.exception("voice_to_text raised for user %s", msg.from_user.id)
            await msg.reply_text("Hubo un error procesando el audio. ¿Lo escribís?")
            return
        if not text:
            missing = missing_inbound_secrets(app.bot_data["skills"].list())
            if is_owner and missing:
                try:
                    link = app.bot_data["vault_web"].new_link()
                    await msg.reply_text(
                        f"Necesito {', '.join(missing)} para procesar audio. Cárgala en la misma "
                        f"red (vence pronto):\n{link}")
                except Exception:
                    await msg.reply_text("No pude procesar ese audio. ¿Lo escribís?")
            else:
                await msg.reply_text(
                    "No pude transcribir ese audio (¿muy largo o error de transcripción?). ¿Lo escribís?")
            return
        await _dispatch(update, text, from_voice=True)

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

    async def _on_connections(update, _context) -> None:
        """/conexiones (owner only): configured MCP servers and web."""
        msg = update.effective_message
        if msg is None or msg.from_user is None:
            return
        if not await _admit(msg):
            return
        if not app.bot_data["gate"].is_owner(str(msg.from_user.id)):
            await msg.reply_text("Ese comando es solo para el dueño.")
            return
        await _reply_parts(msg, app.bot_data["tools"].status_text())

    async def _on_vault(update, _context) -> None:
        """/vault (owner only): reply with a one-time HTTPS link to load secrets."""
        msg = update.effective_message
        if msg is None or msg.from_user is None:
            return
        if not await _admit(msg):
            return
        if not app.bot_data["gate"].is_owner(str(msg.from_user.id)):
            await msg.reply_text("Ese comando es solo para el dueño.")
            return
        link = app.bot_data["vault_web"].new_link()
        await _reply_parts(
            msg, f"Cargá tus credenciales acá (vence pronto; el navegador va a advertir "
                 f"por el certificado, aceptá una vez):\n{link}")

    async def _on_skills(update, _context) -> None:
        """/skills (owner only): list all skills and their status."""
        msg = update.effective_message
        if msg is None or msg.from_user is None or not await _admit(msg):
            return
        if not app.bot_data["gate"].is_owner(str(msg.from_user.id)):
            await msg.reply_text("Ese comando es solo para el dueño.")
            return
        await _reply_parts(msg, app.bot_data["skill_admin"].list_text())

    async def _on_skill_on(update, context) -> None:
        """/skill_on <name> (owner only): enable a skill."""
        msg = update.effective_message
        if msg is None or msg.from_user is None or not await _admit(msg):
            return
        if not app.bot_data["gate"].is_owner(str(msg.from_user.id)):
            await msg.reply_text("Ese comando es solo para el dueño.")
            return
        name = " ".join(context.args).strip()
        if not name:
            await msg.reply_text("Uso: /skill_on <nombre>")
            return
        await _reply_parts(msg, app.bot_data["skill_admin"].enable(name))

    async def _on_skill_off(update, context) -> None:
        """/skill_off <name> (owner only): disable a skill."""
        msg = update.effective_message
        if msg is None or msg.from_user is None or not await _admit(msg):
            return
        if not app.bot_data["gate"].is_owner(str(msg.from_user.id)):
            await msg.reply_text("Ese comando es solo para el dueño.")
            return
        name = " ".join(context.args).strip()
        if not name:
            await msg.reply_text("Uso: /skill_off <nombre>")
            return
        await _reply_parts(msg, app.bot_data["skill_admin"].disable(name))

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
    app.add_handler(CommandHandler("conexiones", _on_connections))
    app.add_handler(CommandHandler("vault", _on_vault))
    app.add_handler(CommandHandler("skills", _on_skills))
    app.add_handler(CommandHandler("skill_on", _on_skill_on))
    app.add_handler(CommandHandler("skill_off", _on_skill_off))
    app.add_handler(CommandHandler(list(ADMIN_COMMANDS), _on_access_admin))
    app.add_handler(CommandHandler(list(LIFECYCLE_COMMANDS), _on_lifecycle))
    app.add_handler(CommandHandler(["code", "fase2"], _on_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_message))
    app.add_handler(MessageHandler(
        filters.VOICE | filters.AUDIO | filters.PHOTO | filters.Document.ALL, _on_message))

    async def _on_error(_update, context) -> None:
        # A Telegram Conflict means a second Ari is polling the same token;
        # stop this one so exactly one instance stays alive and working.
        handle_bot_error(context.error, stop=context.application.stop_running)

    app.add_error_handler(_on_error)
    app.run_polling()

    # run_polling returned: shutdown (incl. closing the DB) is complete.
    if exit_request.get("action") == RESTART:
        logging.info("restarting Ari")
        relaunch(exit_request["chat"])


if __name__ == "__main__":
    main()
