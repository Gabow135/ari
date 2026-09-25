import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from ari.application.schedule.schedule_actions import ScheduleActions, extract_actions
from ari.config.settings import Settings
from ari.domain.agent.agent_service import AgentService
from ari.domain.agent.message import Message
from ari.domain.schedule.actions import CreateAction, parse_action
from ari.infrastructure.claude_env import claude_cli_env
from ari.infrastructure.llm.claude_code_adapter import ClaudeCodeCliAdapter
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore
from ari.infrastructure.soul.soul_loader import SoulLoader

TZ = ZoneInfo("America/Guayaquil")


@pytest.mark.slow
async def test_real_claude_emits_valid_reminder_block():
    now = datetime.now(timezone.utc)
    conn = await connect(":memory:", embedding_dim=4)
    try:
        actions = ScheduleActions(SqliteScheduleStore(conn), TZ, 20, clock=lambda: now)
        system = AgentService().build_prompt(
            [], None, [], soul=SoulLoader("soul")(), is_owner=True,
            extra=await actions.context("u1"))
        s = Settings()
        llm = ClaudeCodeCliAdapter(cli_env=claude_cli_env(s.claude_oauth_token,
                                                          s.claude_config_dir))
        reply = await llm.complete(system, [Message("u1", "user",
                                                    "recuérdame en 5 minutos probar Ari",
                                                    now)])
    finally:
        await conn.close()
    _clean, blocks = extract_actions(reply)
    assert len(blocks) == 1, reply
    act = parse_action(json.loads(blocks[0]), now, TZ)
    assert isinstance(act, CreateAction) and act.at is not None
    assert timedelta(minutes=3) <= act.at - now <= timedelta(minutes=7)
