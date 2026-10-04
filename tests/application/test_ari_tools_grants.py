# tests/application/test_ari_tools_grants.py
import pytest

from ari.application.ari_tools import Actor, AriTools
from ari.application.grants.grant_policy import GrantPolicy
from ari.domain.grants.entities import ACT
from ari.infrastructure.access.sqlite_access_store import SqliteAccessStore
from ari.infrastructure.grants.sqlite_grant_store import SqliteGrantStore
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore
from ari.infrastructure.persistence.sqlite_turn_log import SqliteTurnLog


class _TZ:
    key = "UTC"


def _actor(uid="A", name="Ana"):
    return Actor(uid, uid, name, False, "chat", "turn-1")


async def _tools(tmp_path, actor):
    conn = await connect(str(tmp_path / "ari.db"))
    access = SqliteAccessStore(conn)
    # A (actor) and B both approved so @-resolution works.
    await access.create_pending("A", "ana", "CODEAAAA")
    await access.set_status("A", "approved")
    await access.create_pending("B", "beto", "CODEBBBB")
    await access.set_status("B", "approved")
    from zoneinfo import ZoneInfo
    tools = AriTools(
        actor, schedule=SqliteScheduleStore(conn),
        memory=None, turn_log=SqliteTurnLog(conn), tz=ZoneInfo("UTC"),
        max_items=20, clock=None, access=access,
        grants=GrantPolicy(SqliteGrantStore(conn)))
    return tools, conn


@pytest.mark.asyncio
async def test_compartir_creates_act_grant_and_notifies(tmp_path):
    tools, conn = await _tools(tmp_path, _actor())
    try:
        out = await tools.compartir("recordatorios", "@beto", "act")
        assert "beto" in out.lower()
        assert await tools._grants.allows("B", "A", "schedule", ACT) is True
        notes = await conn.execute_fetchall("SELECT chat_id, text FROM outbox")
        assert notes and notes[0]["chat_id"] == "B"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_compartir_unknown_capability(tmp_path):
    tools, conn = await _tools(tmp_path, _actor())
    try:
        out = await tools.compartir("correo", "@beto", "act")
        assert "recordatorios" in out.lower()
        assert await tools._grants.allows("B", "A", "schedule", ACT) is False
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_compartir_with_self_rejected(tmp_path):
    tools, conn = await _tools(tmp_path, _actor())
    try:
        out = await tools.compartir("recordatorios", "@ana", "act")
        assert "mismo" in out.lower()
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_ver_permisos_lists_both_directions(tmp_path):
    tools, conn = await _tools(tmp_path, _actor())
    try:
        await tools.compartir("recordatorios", "@beto", "read")
        await tools._grants.share("B", "A", "schedule", "act")  # B shared with A
        out = await tools.ver_permisos()
        assert "Diste" in out and "Te dieron" in out
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_revocar_permiso(tmp_path):
    tools, conn = await _tools(tmp_path, _actor())
    try:
        await tools.compartir("recordatorios", "@beto", "act")
        out = await tools.revocar_permiso("recordatorios", "@beto")
        assert "revoqu" in out.lower()
        assert await tools._grants.allows("B", "A", "schedule", "read") is False
        out2 = await tools.revocar_permiso("recordatorios", "@beto")
        assert "no tenía permiso" in out2.lower()
    finally:
        await conn.close()
