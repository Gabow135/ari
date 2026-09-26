import sys
from datetime import datetime, timezone
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
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_coding_requests import SqliteCodingRequests
from ari.infrastructure.persistence.sqlite_turn_log import SqliteTurnLog
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore
from ari.infrastructure.tools.mcp_registry import McpRegistry
from ari.infrastructure.tools.turn_config import TurnConfigWriter

TZ = ZoneInfo("America/Guayaquil")


@pytest.mark.slow
async def test_owner_request_to_implement_queues_a_plan(tmp_path):
    db = str(tmp_path / "ari.db")
    conn = await connect(db, embedding_dim=4)
    try:
        policy = ToolPolicy(
            McpRegistry(str(tmp_path / "none.json"), ".env", str(tmp_path / "out")),
            is_owner=lambda uid: True,
            ari=AriServerSpec(sys.executable, ("-m", "ari.mcp_server"),
                              {"ARI_DB_PATH": db, "ARI_TIMEZONE": "America/Guayaquil",
                               "ARI_MAX_ITEMS": "20", "ARI_OWNER_IDS": "42"}),
            writer=TurnConfigWriter(str(tmp_path / "turns")))
        s = Settings()
        llm = ClaudeCodeCliAdapter(cli_env=claude_cli_env(s.claude_oauth_token,
                                                          s.claude_config_dir), timeout=240)
        now = datetime.now(timezone.utc)
        context = await ScheduleActions(SqliteScheduleStore(conn), TZ, 20,
                                        clock=lambda: now).context("42", CHAT, True)
        question = ("Implementa en tu repositorio un archivo nuevo sum.py con una función "
                    "sum_two(a, b) que devuelva la suma. Prepara el plan.")
        async with policy.turn("42", CHAT, "Gabriel", "42") as turn:
            system = AgentService().build_prompt([], None, [], is_owner=True, extra=context,
                                                 tools=turn.view)
            reply = await llm.complete(system, [Message("42", "user", question, now)],
                                       toolset=turn.toolset)
        requests = await SqliteCodingRequests(conn).claim_pending()
        receipts = await SqliteTurnLog(conn).receipts(turn.turn_id)
    finally:
        await conn.close()
    assert len(requests) == 1, reply
    assert "sum" in requests[0].instruction.lower()
    assert receipts and receipts[0].startswith("🛠️ Preparando plan")
