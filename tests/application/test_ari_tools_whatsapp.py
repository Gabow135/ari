# tests/application/test_ari_tools_whatsapp.py
import pytest

from ari.application.ari_tools import AriTools, DENIED
from ari.domain.whatsapp.entities import InboundWhatsApp
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_whatsapp import SqliteWhatsApp


class Actor:
    def __init__(self, is_owner=True):
        self.user_id, self.chat_id, self.turn_id = "111", "111", "t1"
        self.context, self.is_owner = "chat", is_owner


class FakeLog:
    async def add_receipt(self, turn_id, text):
        return None


@pytest.fixture
async def tools_and_store():
    conn = await connect(":memory:", embedding_dim=8)
    store = SqliteWhatsApp(conn)
    tools = AriTools.__new__(AriTools)        # minimal wiring for these methods
    tools._a = Actor()
    tools._log = FakeLog()
    tools._whatsapp = store
    yield tools, store
    await conn.close()


async def test_pendientes_lists_messages(tools_and_store):
    tools, store = tools_and_store
    await store.record_inbound(InboundWhatsApp("x@s.whatsapp.net", "Juan", "hola"))
    out = await tools.whatsapp_pendientes()
    assert "Juan" in out and "hola" in out


async def test_responder_creates_draft_and_sends_nothing(tools_and_store):
    tools, store = tools_and_store
    rid = await store.record_inbound(InboundWhatsApp("x@s.whatsapp.net", "Ana", "pago?"))
    out = await tools.whatsapp_responder(rid, "decile que sí")
    assert "Ana" in out and "¿" in out           # asks for confirmation
    assert await store.claim_queued() is None     # nothing queued yet


async def test_enviar_queues_the_draft_once(tools_and_store):
    tools, store = tools_and_store
    rid = await store.record_inbound(InboundWhatsApp("x@s.whatsapp.net", "Ana", "pago?"))
    did = await store.create_draft(rid, "x@s.whatsapp.net", "Ana", "sí")
    out = await tools.whatsapp_enviar(did)
    assert "Ana" in out
    assert await store.claim_queued() is not None     # now queued
    # a second enviar on the same draft refuses
    assert "no" in (await tools.whatsapp_enviar(did)).lower()


async def test_enviar_unknown_draft_refuses(tools_and_store):
    tools, _ = tools_and_store
    assert "no" in (await tools.whatsapp_enviar(999)).lower()


async def test_non_owner_denied(tools_and_store):
    tools, store = tools_and_store
    tools._a = Actor(is_owner=False)
    assert await tools.whatsapp_pendientes() == DENIED
    assert await tools.whatsapp_responder(1, "x") == DENIED
    assert await tools.whatsapp_enviar(1) == DENIED
    assert await tools.whatsapp_filtro("ver") == DENIED


async def test_filtro_add_and_view(tools_and_store):
    tools, _ = tools_and_store
    await tools.whatsapp_filtro("agregar_contacto", "Juan")
    out = await tools.whatsapp_filtro("ver")
    assert "Juan" in out
