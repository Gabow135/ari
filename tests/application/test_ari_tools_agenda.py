from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from ari.application.ari_tools import DENIED, Actor, AriTools
from ari.domain.schedule.entities import CANCELLED, REMINDER
from ari.domain.tools.ari_permissions import CHAT, HEARTBEAT, TASK
from ari.infrastructure.memory.sqlite_memory_adapter import SqliteMemoryAdapter
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_turn_log import SqliteTurnLog
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore

TZ = ZoneInfo("America/Guayaquil")
NOW = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)  # vie 15:00 local


@pytest.fixture
async def deps():
    conn = await connect(":memory:", embedding_dim=4)
    yield (SqliteScheduleStore(conn), SqliteMemoryAdapter(conn, embedding_dim=4),
           SqliteTurnLog(conn))
    await conn.close()


def _tools(deps, user="u1", owner=False, context=CHAT, turn="t1", max_items=20):
    schedule, memory, log = deps
    actor = Actor(user, f"c-{user}", "Gabriel", owner, context, turn)
    return AriTools(actor, schedule=schedule, memory=memory, turn_log=log, tz=TZ,
                    max_items=max_items, clock=lambda: NOW)


async def test_agendar_stores_for_actor_and_writes_receipt(deps):
    schedule, _, log = deps
    out = await _tools(deps).agendar("recordatorio", "llamar a Juan",
                                     at="2026-09-26T09:00:00-05:00")
    assert out == "✅ Recordatorio #1: llamar a Juan — sáb 26/09 09:00"
    item = await schedule.get(1)
    assert (item.user_id, item.chat_id, item.kind) == ("u1", "c-u1", REMINDER)
    assert await log.receipts("t1") == [out]


async def test_agendar_recurring_task(deps):
    out = await _tools(deps).agendar("tarea", "resumen", cron="0 8 * * 1")
    assert out == "🔁 Tarea #1 (cada lunes 08:00): resumen — próxima: lun 28/09 08:00"


async def test_agendar_invalid_explains_and_writes_nothing(deps):
    _, _, log = deps
    out = await _tools(deps).agendar("recordatorio", "x", at="2020-01-01T00:00")
    assert out.startswith("No pude agendarlo: la fecha ya pasó")
    assert await log.receipts("t1") == []
    assert (await _tools(deps).agendar("otra", "x", at="2026-09-26T09:00")).startswith(
        "No pude agendarlo")


async def test_cap(deps):
    t = _tools(deps, max_items=1)
    await t.agendar("recordatorio", "a", at="2026-09-26T09:00")
    assert "ya tienes 1" in await t.agendar("recordatorio", "b", at="2026-09-26T10:00")


async def test_listar_and_cancel_only_own(deps):
    schedule, _, log = deps
    await _tools(deps).agendar("recordatorio", "mío", at="2026-09-26T09:00")
    assert "#1 · sáb 26/09 09:00 · mío" in await _tools(deps).listar_agenda()
    assert "No encontré el #1" in await _tools(deps, user="u2").cancelar(1)
    assert await _tools(deps, turn="t2").cancelar(1) == "🗑️ Cancelado #1: mío"
    assert (await schedule.get(1)).status == CANCELLED
    assert await log.receipts("t2") == ["🗑️ Cancelado #1: mío"]
    assert "No tienes" in await _tools(deps).listar_agenda()


async def test_memory_tools(deps):
    t = _tools(deps)
    assert await t.recordar_dato("color favorito", "azul") == "🧠 Guardé: color favorito = azul"
    assert "color favorito: azul" in await t.ver_datos()
    assert await t.olvidar_dato("color favorito") == "🧹 Olvidé: color favorito"
    assert "No tenía guardado" in await t.olvidar_dato("color favorito")
    assert "No tengo datos" in await t.ver_datos()
    assert "falta" in await t.recordar_dato("", "x")


@pytest.mark.parametrize("context", [TASK, HEARTBEAT])
async def test_write_tools_denied_outside_chat(deps, context):
    t = _tools(deps, context=context, owner=True)
    assert await t.agendar("recordatorio", "x", at="2026-09-26T09:00") == DENIED
    assert await t.cancelar(1) == DENIED
    assert await t.recordar_dato("a", "b") == DENIED
    assert await t.olvidar_dato("a") == DENIED
    assert "No tienes" in await t.listar_agenda()  # read-only still works


async def test_cancelar_invalid_id_string(deps):
    _, _, log = deps
    out = await _tools(deps).cancelar("abc")
    assert "No encontré" in out
    assert await log.receipts("t1") == []


async def test_cancelar_invalid_id_none(deps):
    _, _, log = deps
    out = await _tools(deps).cancelar(None)
    assert "No encontré" in out
    assert await log.receipts("t1") == []


async def test_recordar_dato_key_too_long(deps):
    _, _, log = deps
    out = await _tools(deps).recordar_dato("a" * 101, "x")
    assert "demasiado largo" in out
    assert await log.receipts("t1") == []


async def test_recordar_dato_value_too_long(deps):
    _, _, log = deps
    out = await _tools(deps).recordar_dato("a", "x" * 501)
    assert "demasiado largo" in out
    assert await log.receipts("t1") == []
