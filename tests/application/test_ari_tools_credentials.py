# tests/application/test_ari_tools_credentials.py
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from ari.application.ari_tools import DENIED, Actor, AriTools
from ari.domain.tools.ari_permissions import CHAT, HEARTBEAT, TASK
from ari.infrastructure.memory.sqlite_memory_adapter import SqliteMemoryAdapter
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_credential_requests import SqliteCredentialRequests
from ari.infrastructure.persistence.sqlite_turn_log import SqliteTurnLog
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore

TZ = ZoneInfo("America/Guayaquil")
NOW = datetime(2026, 9, 27, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
async def env():
    conn = await connect(":memory:", embedding_dim=4)
    yield conn, SqliteCredentialRequests(conn), SqliteTurnLog(conn)
    await conn.close()


def _tools(env, owner=True, context=CHAT):
    conn, credentials, log = env
    actor = Actor("42", "42", "Gabriel", owner, context, "t1")
    return AriTools(actor, schedule=SqliteScheduleStore(conn),
                    memory=SqliteMemoryAdapter(conn, embedding_dim=4), turn_log=log, tz=TZ,
                    max_items=20, clock=lambda: NOW, credentials=credentials)


async def test_owner_chat_queues_request_and_writes_receipt(env):
    _, credentials, log = env
    out = await _tools(env).pedir_credenciales("el api de groq")
    assert out == "🔑 Te preparo el link seguro para: el api de groq"
    [req] = await credentials.claim_pending()
    assert (req.user_id, req.chat_id, req.requested) == ("42", "42", "el api de groq")
    assert await log.receipts("t1") == [out]


@pytest.mark.parametrize("nombres", ["", "   ", "x" * 501])
async def test_invalid_input_inserts_nothing(env, nombres):
    _, credentials, log = env
    out = await _tools(env).pedir_credenciales(nombres)
    assert out.startswith("No pude prepararlo")
    assert await credentials.claim_pending() == [] and await log.receipts("t1") == []


@pytest.mark.parametrize("owner,context", [(False, CHAT), (True, TASK), (True, HEARTBEAT)])
async def test_denied_outside_owner_chat(env, owner, context):
    _, credentials, log = env
    assert await _tools(env, owner, context).pedir_credenciales("groq") == DENIED
    assert await credentials.claim_pending() == [] and await log.receipts("t1") == []
