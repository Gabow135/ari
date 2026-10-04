# tests/application/test_ari_tools_on_behalf.py
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from ari.application.ari_tools import Actor, AriTools
from ari.application.grants.grant_policy import GrantPolicy
from ari.infrastructure.access.sqlite_access_store import SqliteAccessStore
from ari.infrastructure.grants.sqlite_grant_store import SqliteGrantStore
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_turn_log import SqliteTurnLog
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore


def _clock():
    return datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


async def _build(tmp_path):
    conn = await connect(str(tmp_path / "ari.db"))
    access = SqliteAccessStore(conn)
    for uid, name in (("A", "ana"), ("B", "beto")):
        await access.create_pending(uid, name, f"CODE{uid}{uid}{uid}{uid}")
        await access.set_status(uid, "approved")
    schedule = SqliteScheduleStore(conn)
    grants = GrantPolicy(SqliteGrantStore(conn))

    def tools_for(uid):
        return AriTools(
            Actor(uid, uid, uid, False, "chat", f"turn-{uid}"),
            schedule=schedule, memory=None, turn_log=SqliteTurnLog(conn),
            tz=ZoneInfo("UTC"), max_items=20, clock=_clock,
            access=access, grants=grants)

    return conn, schedule, grants, tools_for


@pytest.mark.asyncio
async def test_list_on_behalf_requires_read_grant(tmp_path):
    conn, schedule, grants, tools_for = await _build(tmp_path)
    try:
        await schedule.add("A", "A", "recordatorio", "pagar luz",
                           _clock(), None)
        b = tools_for("B")
        denied = await b.listar_agenda(de_usuario="@ana")
        assert "permiso" in denied.lower()
        await grants.share("A", "B", "schedule", "read")
        ok = await b.listar_agenda(de_usuario="@ana")
        assert "pagar luz" in ok
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_agendar_on_behalf_needs_act_and_writes_under_target(tmp_path):
    conn, schedule, grants, tools_for = await _build(tmp_path)
    try:
        b = tools_for("B")
        await grants.share("A", "B", "schedule", "read")  # read is not enough
        denied = await b.agendar("recordatorio", "cita", at="2026-10-04T09:00",
                                 de_usuario="@ana")
        assert "permiso" in denied.lower()
        await grants.share("A", "B", "schedule", "act")
        ok = await b.agendar("recordatorio", "cita", at="2026-10-04T09:00",
                             de_usuario="@ana")
        assert "cita" in ok
        items = await schedule.list_for_user("A")
        assert len(items) == 1 and items[0].user_id == "A" and items[0].chat_id == "A"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_unresolvable_target_errors_without_touching_data(tmp_path):
    conn, schedule, grants, tools_for = await _build(tmp_path)
    try:
        b = tools_for("B")
        out = await b.listar_agenda(de_usuario="@nope")
        assert "no encontr" in out.lower()
        assert await b.listar_agenda(de_usuario="@") != ""  # bare @ also errors cleanly
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_cancelar_on_behalf_verifies_ownership(tmp_path):
    conn, schedule, grants, tools_for = await _build(tmp_path)
    try:
        own_b = await schedule.add("B", "B", "recordatorio", "mío de B",
                                   _clock(), None)
        await grants.share("A", "B", "schedule", "act")
        b = tools_for("B")
        # B targets A but passes an id that belongs to B → must not cancel it.
        out = await b.cancelar(own_b, de_usuario="@ana")
        assert "no encontr" in out.lower()
        still = await schedule.get(own_b)
        assert still.status == "active"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_revoked_grant_denies_next_call(tmp_path):
    conn, schedule, grants, tools_for = await _build(tmp_path)
    try:
        await grants.share("A", "B", "schedule", "read")
        b = tools_for("B")
        assert "permiso" not in (await b.listar_agenda(de_usuario="@ana")).lower()
        await grants.revoke("A", "B", "schedule")
        assert "permiso" in (await b.listar_agenda(de_usuario="@ana")).lower()
    finally:
        await conn.close()
