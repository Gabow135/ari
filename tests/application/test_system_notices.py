from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from ari.application.schedule.system_notices import SystemNotices
from ari.domain.schedule.entities import TASK, ScheduleItem
from ari.infrastructure.access.sqlite_access_store import SqliteAccessStore
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore

TZ = ZoneInfo("America/Guayaquil")
DAY = datetime(2026, 9, 25, 17, 0, tzinfo=timezone.utc)  # 12:00 local
NIGHT = datetime(2026, 9, 26, 4, 0, tzinfo=timezone.utc)  # 23:00 local


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
async def stores():
    conn = await connect(":memory:", embedding_dim=4)
    yield SqliteScheduleStore(conn), SqliteAccessStore(conn)
    await conn.close()


def _notices(stores, clock):
    kv, access = stores
    sent = []

    async def send(chat_id, text):
        sent.append((chat_id, text))

    return SystemNotices(kv, access, {"42"}, send, TZ, (22, 7), clock), sent


async def test_emit_sends_to_owner_by_day(stores):
    n, sent = _notices(stores, Clock(DAY))
    await n.emit("hola")
    assert sent == [("42", "hola")]


async def test_quiet_hours_queue_then_flush_together(stores):
    clock = Clock(NIGHT)
    n, sent = _notices(stores, clock)
    await n.emit("uno")
    await n.emit("dos")
    await n.tick()
    assert sent == []
    clock.now = NIGHT + timedelta(hours=8)  # 07:00 local
    await n.tick()
    assert len(sent) == 1 and "• uno" in sent[0][1] and "• dos" in sent[0][1]
    await n.tick()
    assert len(sent) == 1


async def test_stale_access_request_notified_once(stores):
    kv, access = stores
    await access.create_pending("7", "juan", "K7QMX3PA")
    clock = Clock(datetime.now(timezone.utc) + timedelta(hours=1))
    n, sent = _notices(stores, clock)
    await n.tick()
    assert sent == []
    clock.now += timedelta(hours=12)
    await n.tick()
    await n.tick()
    assert len(sent) == 1
    assert "@juan" in sent[0][1] and "/aprobar K7QM-X3PA" in sent[0][1]


async def test_cli_down_and_up_once_each(stores):
    n, sent = _notices(stores, Clock(DAY))
    await n.cli_failed("Not logged in")
    await n.cli_failed("Not logged in")
    await n.cli_recovered()
    await n.cli_recovered()
    assert [t for _, t in sent] == [
        "⚠️ Estoy teniendo problemas con Claude: «Not logged in». Revisa el token o la conexión.",
        "✅ Claude volvió a responder."]


async def test_task_paused(stores):
    n, sent = _notices(stores, Clock(DAY))
    item = ScheduleItem(12, "u1", "c1", TASK, "resumen", DAY, None, "paused", 3)
    await n.task_paused(item, "timeout")
    assert sent == [("42", "⏸️ Pausé la tarea #12 (resumen) porque falló 3 veces: timeout")]
