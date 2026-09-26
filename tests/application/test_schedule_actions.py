from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from ari.application.schedule.agenda_format import created_receipt, item_line
from ari.application.schedule.schedule_actions import ScheduleActions, extract_actions
from ari.domain.schedule.entities import PAUSED, REMINDER, TASK, ScheduleItem
from ari.domain.tools.ari_permissions import CHAT
from ari.domain.tools.ari_permissions import TASK as TASK_CONTEXT
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore

TZ = ZoneInfo("America/Guayaquil")
NOW = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)  # vie 15:00 local
AT = datetime(2026, 9, 26, 14, 0, tzinfo=timezone.utc)  # sáb 09:00 local


@pytest.fixture
async def store():
    conn = await connect(":memory:", embedding_dim=4)
    yield SqliteScheduleStore(conn)
    await conn.close()


def test_extract_strips_blocks_including_unclosed():
    clean, blocks = extract_actions('Listo. <ari-action>{"a":1}</ari-action>\n<ari-action>{"type":')
    assert clean == "Listo." and len(blocks) == 1


def test_item_line_formats():
    one = ScheduleItem(12, "u", "c", REMINDER, "llamar a Juan", AT, None, "active")
    assert item_line(one, TZ) == "#12 · sáb 26/09 09:00 · llamar a Juan"
    rec = ScheduleItem(13, "u", "c", TASK, "resumen",
                       datetime(2026, 9, 28, 13, 0, tzinfo=timezone.utc), "0 8 * * 1", PAUSED)
    assert item_line(rec, TZ) == ("#13 · cada lunes 08:00 (próxima lun 28/09 08:00) · resumen"
                                  " · ⏸️ pausada")


def test_created_receipts():
    assert created_receipt(12, REMINDER, "llamar a Juan", AT, None, TZ) == \
        "✅ Recordatorio #12: llamar a Juan — sáb 26/09 09:00"
    nxt = datetime(2026, 9, 28, 13, 0, tzinfo=timezone.utc)
    assert created_receipt(13, TASK, "resumen", nxt, "0 8 * * 1", TZ) == \
        "🔁 Tarea #13 (cada lunes 08:00): resumen — próxima: lun 28/09 08:00"


async def test_context_has_time_and_tools_hint_but_no_block_format(store):
    ctx = await ScheduleActions(store, TZ, 20, clock=lambda: NOW).context("u1")
    assert "viernes 25/09/2026 15:00 (America/Guayaquil)" in ctx
    assert "agendar" in ctx and "listar_agenda" in ctx
    assert "<ari-action>" not in ctx


async def test_user_chat_context_lists_only_user_tools_and_no_injection_rule(store):
    ctx = await ScheduleActions(store, TZ, 20, clock=lambda: NOW).context(
        "u1", CHAT, is_owner=False)
    assert "agendar" in ctx and "listar_agenda" in ctx and "recordar_dato" in ctx
    assert "aprobar_acceso" not in ctx and "enviar_mensaje" not in ctx
    assert "solo si tu creador lo pidió" not in ctx


async def test_owner_chat_context_lists_admin_tools_and_injection_rule(store):
    ctx = await ScheduleActions(store, TZ, 20, clock=lambda: NOW).context(
        "42", CHAT, is_owner=True)
    assert "aprobar_acceso" in ctx and "revocar_acceso" in ctx and "enviar_mensaje" in ctx
    assert "solo si tu creador lo pidió" in ctx


async def test_task_context_is_read_only_and_has_no_write_tools(store):
    ctx = await ScheduleActions(store, TZ, 20, clock=lambda: NOW).context(
        "u1", TASK_CONTEXT, is_owner=False)
    assert "listar_agenda" in ctx and "ver_datos" in ctx
    assert "agendar" not in ctx and "recordar_dato" not in ctx
    assert "solo lectura" in ctx


async def test_list_text(store):
    actions = ScheduleActions(store, TZ, 20, clock=lambda: NOW)
    assert "No tienes" in await actions.list_text("u1")
    await store.add("u1", "c1", REMINDER, "llamar a Juan", AT, None)
    assert "#1 · sáb 26/09 09:00 · llamar a Juan" in await actions.list_text("u1")
