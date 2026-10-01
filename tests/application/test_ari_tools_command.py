from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from ari.application.ari_tools import DENIED, Actor, AriTools
from ari.domain.tools.ari_permissions import CHAT, HEARTBEAT, TASK
from ari.infrastructure.memory.sqlite_memory_adapter import SqliteMemoryAdapter
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_command_requests import SqliteCommandRequests
from ari.infrastructure.persistence.sqlite_turn_log import SqliteTurnLog
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore

TZ = ZoneInfo("America/Guayaquil")
NOW = datetime(2026, 9, 26, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
async def env():
    conn = await connect(":memory:", embedding_dim=4)
    yield conn, SqliteCommandRequests(conn), SqliteTurnLog(conn)
    await conn.close()


def _tools(env, owner=True, context=CHAT):
    conn, commands, log = env
    actor = Actor("42", "42", "Gabriel", owner, context, "t1")
    return AriTools(actor, schedule=SqliteScheduleStore(conn),
                    memory=SqliteMemoryAdapter(conn, embedding_dim=4), turn_log=log, tz=TZ,
                    max_items=20, clock=lambda: NOW, commands=commands)


async def test_owner_chat_queues_command_and_writes_receipt(env):
    _, commands, log = env
    out = await _tools(env).proponer_comando("pytest -q")
    assert out == "⌨️ Preparando comando: pytest -q"
    [req] = await commands.claim_pending()
    assert (req.user_id, req.chat_id, req.command) == ("42", "42", "pytest -q")
    assert await log.receipts("t1") == [out]


@pytest.mark.parametrize("comando", ["", "   ", "x" * 2001])
async def test_invalid_input_inserts_nothing(env, comando):
    _, commands, log = env
    out = await _tools(env).proponer_comando(comando)
    assert out.startswith("No pude prepararlo")
    assert await commands.claim_pending() == [] and await log.receipts("t1") == []


@pytest.mark.parametrize("owner,context", [(False, CHAT), (True, TASK), (True, HEARTBEAT)])
async def test_denied_outside_owner_chat(env, owner, context):
    _, commands, log = env
    assert await _tools(env, owner, context).proponer_comando("ls") == DENIED
    assert await commands.claim_pending() == [] and await log.receipts("t1") == []
