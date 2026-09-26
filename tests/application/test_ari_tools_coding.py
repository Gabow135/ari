from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from ari.application.ari_tools import DENIED, Actor, AriTools
from ari.domain.tools.ari_permissions import CHAT, HEARTBEAT, TASK
from ari.infrastructure.memory.sqlite_memory_adapter import SqliteMemoryAdapter
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_coding_requests import SqliteCodingRequests
from ari.infrastructure.persistence.sqlite_turn_log import SqliteTurnLog
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore

TZ = ZoneInfo("America/Guayaquil")
NOW = datetime(2026, 9, 26, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
async def env():
    conn = await connect(":memory:", embedding_dim=4)
    yield conn, SqliteCodingRequests(conn), SqliteTurnLog(conn)
    await conn.close()


def _tools(env, owner=True, context=CHAT):
    conn, coding, log = env
    actor = Actor("42", "42", "Gabriel", owner, context, "t1")
    return AriTools(actor, schedule=SqliteScheduleStore(conn),
                    memory=SqliteMemoryAdapter(conn, embedding_dim=4), turn_log=log, tz=TZ,
                    max_items=20, clock=lambda: NOW, coding=coding)


async def test_owner_chat_queues_request_and_writes_receipt(env):
    _, coding, log = env
    out = await _tools(env).proponer_codigo("agrega un comando /ping", "proyectos/ari")
    assert out == "🛠️ Preparando plan: agrega un comando /ping"
    [req] = await coding.claim_pending()
    assert (req.user_id, req.chat_id, req.instruction, req.target) == (
        "42", "42", "agrega un comando /ping", "proyectos/ari")
    assert await log.receipts("t1") == [out]


async def test_long_instruction_truncated_in_receipt_only(env):
    _, coding, _ = env
    text = "x" * 300
    out = await _tools(env).proponer_codigo(text)
    assert out == "🛠️ Preparando plan: " + "x" * 119 + "…"
    [req] = await coding.claim_pending()
    assert req.instruction == text and req.target is None


@pytest.mark.parametrize("instruccion,carpeta", [("", None), ("   ", None),
                                                 ("x" * 2001, None), ("ok", "c" * 501)])
async def test_invalid_input_inserts_nothing(env, instruccion, carpeta):
    _, coding, log = env
    out = await _tools(env).proponer_codigo(instruccion, carpeta)
    assert out.startswith("No pude prepararlo")
    assert await coding.claim_pending() == [] and await log.receipts("t1") == []


@pytest.mark.parametrize("owner,context", [(False, CHAT), (True, TASK), (True, HEARTBEAT)])
async def test_denied_outside_owner_chat(env, owner, context):
    _, coding, log = env
    assert await _tools(env, owner, context).proponer_codigo("agrega /ping") == DENIED
    assert await coding.claim_pending() == [] and await log.receipts("t1") == []
