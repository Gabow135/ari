from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from ari.application.access.gate import AccessGate
from ari.application.ari_tools import DENIED, Actor, AriTools
from ari.domain.access.entities import APPROVED, PENDING
from ari.domain.schedule.entities import CANCELLED, REMINDER
from ari.domain.tools.ari_permissions import CHAT, HEARTBEAT, TASK
from ari.infrastructure.access.sqlite_access_store import SqliteAccessStore
from ari.infrastructure.memory.sqlite_memory_adapter import SqliteMemoryAdapter
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_turn_log import SqliteTurnLog
from ari.infrastructure.schedule.sqlite_schedule_store import SqliteScheduleStore

TZ = ZoneInfo("America/Guayaquil")
NOW = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)
OWNER = "42"


@pytest.fixture
async def env():
    conn = await connect(":memory:", embedding_dim=4)
    schedule, access = SqliteScheduleStore(conn), SqliteAccessStore(conn)
    await access.create_pending("7", "Juan", "K7QMX3PA")
    await access.create_pending("8", "pedro", "P3DR0XYZ")
    await access.set_status("8", APPROVED)
    yield conn, schedule, access, SqliteTurnLog(conn)
    await conn.close()


def _tools(env, user=OWNER, owner=True, context=CHAT, name="Gabriel", owner_ids=None):
    conn, schedule, access, log = env
    gate = AccessGate(access, owner_ids or {OWNER}, on_revoke=schedule.cancel_user)
    actor = Actor(user, user, name, owner, context, "t1")
    return AriTools(actor, schedule=schedule, memory=SqliteMemoryAdapter(conn, embedding_dim=4),
                    turn_log=log, tz=TZ, max_items=20, clock=lambda: NOW,
                    gate=gate, access=access)


async def test_approve_by_username_case_insensitive(env):
    _, _, access, log = env
    out = await _tools(env).aprobar_acceso("@juan")
    assert "ya tiene acceso" in out
    assert (await access.get("7")).status == APPROVED
    assert await log.receipts("t1") == ["✅ Aprobé a @Juan (id 7)"]
    assert await log.outbox_pending() == [(1, "7", "¡Ya tienes acceso! Escríbeme cuando quieras.", 0)]


async def test_approve_by_code(env):
    _, _, _, log = env
    out = await _tools(env).aprobar_acceso("k7qm-x3pa")
    assert "ya tiene acceso" in out
    assert await log.receipts("t1") == ["✅ Aprobé a @Juan (id 7)"]
    assert await log.outbox_pending() == [
        (1, "7", "¡Ya tienes acceso! Escríbeme cuando quieras.", 0)]


async def test_approve_unknown(env):
    assert "No encontré" in await _tools(env).aprobar_acceso("@nadie")


async def test_approve_already_approved_has_no_side_effects(env):
    _, _, _, log = env
    out = await _tools(env).aprobar_acceso("p3dr-0xyz")  # pedro is already approved
    assert "ya tenía acceso" in out
    assert await log.receipts("t1") == []
    assert await log.outbox_pending() == []


async def test_approve_unknown_code_has_no_side_effects(env):
    _, _, _, log = env
    out = await _tools(env).aprobar_acceso("ZZZZ-ZZZZ")
    assert "Código no encontrado" in out
    assert await log.receipts("t1") == []


async def test_approve_ambiguous_username(env):
    _, _, access, log = env
    await access.create_pending("10", "juan", "ABCDEFGH")  # same username as "7", case-insensitive
    out = await _tools(env).aprobar_acceso("@juan")
    assert "Hay varias solicitudes" in out
    assert await log.receipts("t1") == []
    assert await log.outbox_pending() == []


async def test_revoke_by_username_cancels_items(env):
    _, schedule, access, log = env
    item = await schedule.add("8", "8", REMINDER, "x", NOW, None)
    out = await _tools(env).revocar_acceso("@Pedro")
    assert "revocado" in out.lower()
    assert await access.get("8") is None
    assert (await schedule.get(item)).status == CANCELLED
    assert await log.receipts("t1") == ["⛔ Revoqué a @pedro (id 8)"]


async def test_revoke_denied_by_gate_writes_no_receipt(env):
    _, _, access, log = env
    # actor claims is_owner=True, but "99" is not in the gate's actual owner set.
    t = _tools(env, user="99", owner=True, context=CHAT)
    out = await t.revocar_acceso("@pedro")
    assert "dueño" in out.lower()
    assert (await access.get("8")) is not None
    assert await log.receipts("t1") == []


async def test_revoke_refuses_owner_target(env):
    _, _, access, log = env
    t = _tools(env, owner_ids={OWNER, "8"})  # pedro (id 8) is also a gate owner
    out = await t.revocar_acceso("@pedro")
    assert out == "No puedo hacer eso con la cuenta de un creador."
    assert (await access.get("8")) is not None
    assert await log.receipts("t1") == []


async def test_bare_at_matches_no_one(env):
    _, _, access, log = env
    await access.create_pending("50", None, "NONAMEC1")
    await access.set_status("50", APPROVED)
    assert "No encontré" in await _tools(env).revocar_acceso("@")
    assert await log.receipts("t1") == []


async def test_ver_accesos(env):
    out = await _tools(env).ver_accesos()
    assert "Juan" in out and "pedro" in out


async def test_message_to_approved_user_is_signed(env):
    _, _, _, log = env
    out = await _tools(env).enviar_mensaje("@PEDRO", "la reunión es a las 5")
    assert out == "📨 Enviado a @pedro (id 8)"
    assert await log.outbox_pending() == [
        (1, "8", "📨 De Gabriel (vía Ari): la reunión es a las 5", 0)]
    assert await log.receipts("t1") == [out]


async def test_message_only_to_approved_users(env):
    assert "No encontré" in await _tools(env).enviar_mensaje("@juan", "hola")  # pending
    assert "No encontré" in await _tools(env).enviar_mensaje("999", "hola")
    assert "texto" in await _tools(env).enviar_mensaje("@pedro", "")


async def test_message_bare_at_matches_no_one(env):
    _, _, access, log = env
    await access.create_pending("50", None, "NONAMEC1")
    await access.set_status("50", APPROVED)
    assert "No encontré" in await _tools(env).enviar_mensaje("@", "hola")
    assert await log.outbox_pending() == []


async def test_message_denied_when_actor_not_real_owner(env):
    _, _, _, log = env
    # actor claims is_owner=True, but "99" is not in the gate's actual owner set.
    t = _tools(env, user="99", owner=True, context=CHAT)
    assert await t.enviar_mensaje("@pedro", "hola") == DENIED
    assert await log.outbox_pending() == []


async def test_message_refuses_owner_target(env):
    _, _, access, log = env
    t = _tools(env, owner_ids={OWNER, "8"})  # pedro (id 8) is also a gate owner
    out = await t.enviar_mensaje("@pedro", "hola")
    assert out == "No puedo hacer eso con la cuenta de un creador."
    assert await log.outbox_pending() == []


async def test_message_signature_falls_back_when_name_blank(env):
    _, _, _, log = env
    out = await _tools(env, name="   ").enviar_mensaje("@pedro", "hola")
    assert out == "📨 Enviado a @pedro (id 8)"
    pending = await log.outbox_pending()
    assert pending == [(1, "8", "📨 De tu contacto (vía Ari): hola", 0)]


async def test_ambiguous_username(env):
    _, _, access, _ = env
    await access.create_pending("9", "PEDRO", "ABCDEFGH")
    await access.set_status("9", APPROVED)
    assert "varios" in await _tools(env).enviar_mensaje("@pedro", "hola")
    assert "📨 Enviado" in await _tools(env).enviar_mensaje("9", "hola")  # by id works


@pytest.mark.parametrize("owner,context", [(False, CHAT), (True, TASK), (True, HEARTBEAT)])
async def test_admin_and_messages_denied(env, owner, context):
    _, _, access, log = env
    t = _tools(env, user=OWNER if owner else "8", owner=owner, context=context)
    assert await t.aprobar_acceso("@juan") == DENIED
    assert await t.revocar_acceso("@pedro") == DENIED
    assert await t.enviar_mensaje("@pedro", "hola") == DENIED
    assert await log.receipts("t1") == []
    assert await log.outbox_pending() == []
    assert (await access.get("7")).status == PENDING
    assert (await access.get("8")).status == APPROVED


async def test_heartbeat_owner_can_list_accesses(env):
    assert "Juan" in await _tools(env, context=HEARTBEAT).ver_accesos()
