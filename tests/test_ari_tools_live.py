import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from ari.application.schedule.schedule_actions import ScheduleActions
from ari.application.tools.tool_policy import AriServerSpec, ToolPolicy
from ari.config.settings import Settings
from ari.domain.agent.agent_service import AgentService
from ari.domain.agent.message import Message
from ari.domain.tools.ari_permissions import CHAT
from ari.infrastructure.claude_env import claude_cli_env
from ari.infrastructure.llm.claude_code_adapter import ClaudeCodeCliAdapter
from ari.infrastructure.memory.sqlite_memory_adapter import SqliteMemoryAdapter
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_turn_log import SqliteTurnLog
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore
from ari.infrastructure.tools.mcp_registry import McpRegistry
from ari.infrastructure.tools.turn_config import TurnConfigWriter

TZ = ZoneInfo("America/Guayaquil")


async def _owner_turn(tmp_path, question):
    db = str(tmp_path / "ari.db")
    conn = await connect(db, embedding_dim=4)
    registry = McpRegistry(str(tmp_path / "none.json"), ".env", str(tmp_path / "out"))
    policy = ToolPolicy(registry, is_owner=lambda uid: True,
                        ari=AriServerSpec(sys.executable, ("-m", "ari.mcp_server"),
                                          {"ARI_DB_PATH": db, "ARI_TIMEZONE": "America/Guayaquil",
                                           "ARI_MAX_ITEMS": "20", "ARI_OWNER_IDS": "42"}),
                        writer=TurnConfigWriter(str(tmp_path / "turns")))
    s = Settings()
    llm = ClaudeCodeCliAdapter(cli_env=claude_cli_env(s.claude_oauth_token, s.claude_config_dir),
                               timeout=240)
    now = datetime.now(timezone.utc)
    context = await ScheduleActions(SqliteScheduleStore(conn), TZ, 20,
                                    clock=lambda: now).context("42")
    async with policy.turn("42", CHAT, "Gabriel", "42") as turn:
        system = AgentService().build_prompt([], None, [], is_owner=True, extra=context,
                                             tools=turn.view)
        reply = await llm.complete(system, [Message("42", "user", question, now)],
                                   toolset=turn.toolset)
    return conn, turn.turn_id, reply, now


@pytest.mark.slow
async def test_schedule_via_ari_tool(tmp_path):
    conn, turn_id, reply, now = await _owner_turn(tmp_path, "recuérdame en 5 minutos probar Ari")
    try:
        items = await SqliteScheduleStore(conn).list_for_user("42")
        receipts = await SqliteTurnLog(conn).receipts(turn_id)
    finally:
        await conn.close()
    assert len(items) == 1, reply
    assert timedelta(minutes=3) <= items[0].next_run_at - now <= timedelta(minutes=7)
    assert receipts and receipts[0].startswith("✅ Recordatorio #1")
    assert not os.listdir(tmp_path / "turns")  # per-turn config removed


@pytest.mark.slow
async def test_remember_fact_via_ari_tool(tmp_path):
    conn, turn_id, reply, _ = await _owner_turn(tmp_path,
                                                "recuerda que mi color favorito es azul")
    try:
        facts = await SqliteMemoryAdapter(conn, embedding_dim=4).get_facts("42")
    finally:
        await conn.close()
    assert any("azul" in f.value.lower() for f in facts), reply
