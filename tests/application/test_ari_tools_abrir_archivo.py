from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from ari.application.ari_tools import DENIED, Actor, AriTools
from ari.domain.tools.ari_permissions import CHAT
from ari.infrastructure.memory.sqlite_memory_adapter import SqliteMemoryAdapter
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_file_requests import SqliteFileRequests
from ari.infrastructure.persistence.sqlite_turn_log import SqliteTurnLog
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore

TZ = ZoneInfo("America/Guayaquil")
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
async def env(tmp_path):
    conn = await connect(":memory:", embedding_dim=4)
    yield conn, SqliteFileRequests(conn), SqliteTurnLog(conn), tmp_path
    await conn.close()


def _tools(env, owner=True, denied=None):
    conn, files, log, _tmp = env
    actor = Actor("42", "42", "Gabriel", owner, CHAT, "t1")
    return AriTools(actor, schedule=SqliteScheduleStore(conn),
                    memory=SqliteMemoryAdapter(conn, embedding_dim=4), turn_log=log, tz=TZ,
                    max_items=20, clock=lambda: NOW, files=files, denied_roots=denied or [])


async def test_owner_enqueues_and_receipts(env):
    _conn, files, _log, tmp = env
    f = tmp / "factura.pdf"; f.write_text("x")
    out = await _tools(env).abrir_archivo(str(f))
    assert out.startswith("🔗")
    [req] = await files.claim_pending()
    assert req.path.endswith("factura.pdf")


async def test_non_owner_denied(env):
    _conn, files, _log, tmp = env
    f = tmp / "a.pdf"; f.write_text("x")
    assert await _tools(env, owner=False).abrir_archivo(str(f)) == DENIED
    assert await files.claim_pending() == []


async def test_denied_path_not_enqueued(env):
    _conn, files, _log, tmp = env
    secret = tmp / ".env"; secret.write_text("S=1")
    out = await _tools(env, denied=[]).abrir_archivo(str(secret))
    assert "protegida" in out.lower() or "no comparto" in out.lower()
    assert await files.claim_pending() == []


async def test_missing_file(env):
    out = await _tools(env).abrir_archivo("/no/such/file.pdf")
    assert out.startswith("No existe")
