from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from ari.application.schedule.schedule_actions import ScheduleActions, extract_actions
from ari.domain.schedule.entities import CANCELLED, REMINDER
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore

TZ = ZoneInfo("America/Guayaquil")
NOW = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)  # vie 15:00 local
REMIND = ('<ari-action>{"type":"reminder","at":"2026-09-26T09:00:00-05:00",'
          '"text":"llamar a Juan"}</ari-action>')


@pytest.fixture
async def store():
    conn = await connect(":memory:", embedding_dim=4)
    yield SqliteScheduleStore(conn)
    await conn.close()


@pytest.fixture
def actions(store):
    return ScheduleActions(store, TZ, max_items=2, clock=lambda: NOW)


def test_extract_strips_blocks_including_unclosed():
    clean, blocks = extract_actions(f"Listo. {REMIND}\n<ari-action>{{\"type\":")
    assert clean == "Listo." and len(blocks) == 1
    assert "<ari-action>" not in clean


async def test_apply_stores_and_appends_code_confirmation(actions, store):
    out = await actions.apply("u1", "c1", f"Claro, te lo recuerdo.\n{REMIND}")
    assert out.startswith("Claro, te lo recuerdo.")
    assert "✅ Recordatorio #1: llamar a Juan — sáb 26/09 09:00" in out
    item = await store.get(1)
    assert (item.kind, item.chat_id, item.next_run_at) == (
        REMINDER, "c1", datetime(2026, 9, 26, 14, 0, tzinfo=timezone.utc))


async def test_action_only_reply_still_confirms(actions):
    out = await actions.apply("u1", "c1", REMIND)
    assert out.startswith("✅ Recordatorio #1")


async def test_recurring_confirmation(actions):
    out = await actions.apply("u1", "c1", '<ari-action>{"type":"task","cron":"0 8 * * 1",'
                                          '"text":"resumen"}</ari-action>')
    assert "🔁 Tarea #1 (cada lunes 08:00): resumen — próxima: lun 28/09 08:00" in out


async def test_invalid_block_stores_nothing_and_says_why(actions, store):
    out = await actions.apply("u1", "c1", 'Ok <ari-action>{"type":"reminder",'
                                          '"at":"2020-01-01T00:00","text":"x"}</ari-action>')
    assert "⚠️ No pude agendarlo: la fecha ya pasó" in out
    assert await store.count_active("u1") == 0


async def test_malformed_json_is_reported(actions):
    out = await actions.apply("u1", "c1", "Ok <ari-action>{not json}</ari-action>")
    assert "⚠️ No pude agendarlo" in out


async def test_blocks_ignored_when_not_allowed(actions, store):
    out = await actions.apply("u1", "c1", f"Resultado {REMIND}", allow=False)
    assert out == "Resultado"
    assert await store.count_active("u1") == 0


async def test_cancel_own_but_not_foreign(actions, store):
    await actions.apply("u1", "c1", REMIND)
    foreign = await actions.apply("u2", "c2", '<ari-action>{"type":"cancel","id":1}</ari-action>')
    assert "No encontré el #1" in foreign
    own = await actions.apply("u1", "c1", '<ari-action>{"type":"cancel","id":1}</ari-action>')
    assert "🗑️ Cancelado #1" in own
    assert (await store.get(1)).status == CANCELLED


async def test_per_user_cap(actions):
    await actions.apply("u1", "c1", REMIND)
    await actions.apply("u1", "c1", REMIND)
    out = await actions.apply("u1", "c1", REMIND)
    assert "ya tienes 2" in out


async def test_context_has_time_items_and_format(actions):
    await actions.apply("u1", "c1", REMIND)
    ctx = await actions.context("u1")
    assert "viernes 25/09/2026 15:00 (America/Guayaquil)" in ctx
    assert "#1 · sáb 26/09 09:00 · llamar a Juan" in ctx
    assert "<ari-action>" in ctx


async def test_list_text(actions, store):
    assert "No tienes" in await actions.list_text("u1")
    await actions.apply("u1", "c1", REMIND)
    assert "#1 · sáb 26/09 09:00 · llamar a Juan" in await actions.list_text("u1")
