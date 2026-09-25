import re

import pytest

from ari.application.access.gate import AccessGate, GateResult, deliver, normalize_code
from ari.domain.access.entities import APPROVED
from ari.infrastructure.access.sqlite_access_store import SqliteAccessStore
from ari.infrastructure.persistence.db import connect

OWNER = "42"


@pytest.fixture
async def store():
    conn = await connect(":memory:", embedding_dim=4)
    yield SqliteAccessStore(conn)
    await conn.close()


@pytest.fixture
def gate(store):
    return AccessGate(store, owner_ids={OWNER})


def _code_in(text: str) -> str:
    return re.search(r"[A-Z2-9]{4}-[A-Z2-9]{4}", text).group(0)


async def test_owner_is_always_allowed(gate):
    out = await gate.check(OWNER, "boss")
    assert out.allowed and out.reply is None and out.notifications == []


async def test_new_user_is_blocked_gets_code_and_owners_are_notified(gate):
    out = await gate.check("7", "juan")
    assert not out.allowed
    code = _code_in(out.reply)
    assert [chat for chat, _ in out.notifications] == [OWNER]
    note = out.notifications[0][1]
    assert "@juan" in note and "7" in note and f"/aprobar {code}" in note


async def test_repeat_message_reuses_code_without_renotifying(gate):
    first = await gate.check("7", "juan")
    second = await gate.check("7", "juan")
    assert not second.allowed
    assert _code_in(second.reply) == _code_in(first.reply)
    assert second.notifications == []


async def test_owner_approves_code_then_user_is_allowed(gate, store):
    code = _code_in((await gate.check("7", "juan")).reply)
    out = await gate.admin_command("/aprobar " + code.lower().replace("-", " "), OWNER)
    assert "7" in out.reply
    assert [chat for chat, _ in out.notifications] == ["7"]
    assert (await store.get("7")).status == APPROVED
    assert (await gate.check("7", "juan")).allowed


async def test_invalid_code_is_rejected(gate):
    out = await gate.admin_command("/aprobar ZZZZ-9999", OWNER)
    assert "no encontr" in out.reply.lower()
    assert out.notifications == []


async def test_non_owner_cannot_use_admin_commands(gate, store):
    code = _code_in((await gate.check("7", "juan")).reply)
    out = await gate.admin_command(f"/aprobar {code}", "7")
    assert "solo" in out.reply.lower()
    assert (await store.get("7")).status != APPROVED


async def test_revoke_blocks_user_again_with_new_request(gate):
    code = _code_in((await gate.check("7", "juan")).reply)
    await gate.admin_command(f"/aprobar {code}", OWNER)
    out = await gate.admin_command("/revocar 7", OWNER)
    assert "7" in out.reply
    again = await gate.check("7", "juan")
    assert not again.allowed and again.notifications  # owners re-notified


async def test_revoke_unknown_user(gate):
    out = await gate.admin_command("/revocar 999", OWNER)
    assert "no" in out.reply.lower()


async def test_list_access(gate):
    code = _code_in((await gate.check("7", "juan")).reply)
    await gate.check("8", None)
    await gate.admin_command(f"/aprobar {code}", OWNER)
    out = await gate.admin_command("/accesos", OWNER)
    assert "juan" in out.reply and "8" in out.reply


async def test_missing_argument_shows_usage(gate):
    assert "uso" in (await gate.admin_command("/aprobar", OWNER)).reply.lower()


async def test_deliver_sends_reply_and_survives_failed_notification():
    replies, sent = [], []

    async def reply(text):
        replies.append(text)

    async def send(chat_id, text):
        if chat_id == "bad":
            raise RuntimeError("Forbidden: bot can't initiate conversation")
        sent.append((chat_id, text))

    result = GateResult(reply="hola", notifications=[("bad", "x"), ("42", "y")])
    await deliver(result, reply, send)
    assert replies == ["hola"] and sent == [("42", "y")]


def test_normalize_code():
    assert normalize_code(" k7qm-x3pa ") == "K7QMX3PA"
    assert normalize_code("K7QM X3PA") == "K7QMX3PA"


async def test_revoke_calls_on_revoke(store):
    revoked = []

    async def on_revoke(user_id):
        revoked.append(user_id)

    gate = AccessGate(store, owner_ids={OWNER}, on_revoke=on_revoke)
    code = _code_in((await gate.check("7", "juan")).reply)
    await gate.admin_command(f"/aprobar {code}", OWNER)
    await gate.admin_command("/revocar 7", OWNER)
    assert revoked == ["7"]
