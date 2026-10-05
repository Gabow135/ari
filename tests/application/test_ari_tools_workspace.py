# tests/application/test_ari_tools_workspace.py
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from ari.application.ari_tools import DENIED, Actor, AriTools
from ari.domain.tools.ari_permissions import CHAT, TASK
from ari.infrastructure.memory.sqlite_memory_adapter import SqliteMemoryAdapter
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_turn_log import SqliteTurnLog
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore
from ari.infrastructure.workspace.user_workspace import Workspaces

TZ = ZoneInfo("America/Guayaquil")
NOW = datetime(2026, 10, 4, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
async def env(tmp_path):
    conn = await connect(":memory:", embedding_dim=4)
    yield conn, Workspaces(str(tmp_path)), SqliteTurnLog(conn)
    await conn.close()


def _tools(env, *, owner=True, context=CHAT, sandbox=None, runner=None):
    conn, workspaces, log = env
    actor = Actor("42", "42", "Gabriel", owner, context, "t1")
    return AriTools(actor, schedule=SqliteScheduleStore(conn),
                    memory=SqliteMemoryAdapter(conn, embedding_dim=4), turn_log=log, tz=TZ,
                    max_items=20, clock=lambda: NOW,
                    workspaces=workspaces, sql_sandbox=sandbox, runner=runner)


async def test_write_then_read_roundtrip(env):
    t = _tools(env)
    assert await t.escribir_archivo("notas/plan.txt", "hola") == "📝 Guardé notas/plan.txt"
    assert await t.leer_archivo("notas/plan.txt") == "hola"


async def test_list_and_delete(env):
    t = _tools(env)
    await t.escribir_archivo("a.txt", "x")
    assert "a.txt" in await t.listar_archivos(".")
    assert await t.borrar_archivo("a.txt") == "🗑️ Borré a.txt"
    assert (await t.leer_archivo("a.txt")).startswith("No existe")


async def test_traversal_is_refused(env):
    t = _tools(env)
    out = await t.escribir_archivo("../escape.txt", "x")
    assert out.startswith("No pude escribir")


async def test_denied_outside_chat(env):
    out = await _tools(env, owner=True, context=TASK).escribir_archivo("a.txt", "x")
    assert out == DENIED


# ---- SQL tests ----------------------------------------------------------------

from ari.infrastructure.workspace.sqlite_sandbox import SqliteSandbox  # noqa: E402


async def test_sql_create_insert_select(env):
    t = _tools(env, sandbox=SqliteSandbox())
    assert (await t.consultar_sql("datos.sqlite", "CREATE TABLE t (a INT)")).startswith("✅")
    await t.consultar_sql("datos.sqlite", "INSERT INTO t VALUES (1),(2)")
    out = await t.consultar_sql("datos.sqlite", "SELECT a FROM t ORDER BY a")
    assert "a" in out and "1" in out and "2" in out


async def test_sql_attach_is_reported_as_error(env):
    t = _tools(env, sandbox=SqliteSandbox())
    out = await t.consultar_sql("datos.sqlite", "ATTACH DATABASE 'x.sqlite' AS x")
    assert out.startswith("Error de SQL")


async def test_sql_db_path_is_jailed(env):
    t = _tools(env, sandbox=SqliteSandbox())
    out = await t.consultar_sql("../escape.sqlite", "CREATE TABLE t (a INT)")
    assert out.startswith("Ruta de base inválida")
