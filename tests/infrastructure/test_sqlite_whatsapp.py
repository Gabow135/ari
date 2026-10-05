import pytest

from ari.domain.whatsapp.entities import InboundWhatsApp
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_whatsapp import SqliteWhatsApp


@pytest.fixture
async def store():
    conn = await connect(":memory:", embedding_dim=8)
    yield SqliteWhatsApp(conn)
    await conn.close()


async def test_record_and_list_pending(store):
    rid = await store.record_inbound(
        InboundWhatsApp("549111@s.whatsapp.net", "Juan", "hola", "", False))
    rows = await store.list_pending(10)
    assert rows[0].id == rid and rows[0].contact_name == "Juan" and rows[0].text == "hola"


async def test_draft_then_queue_marks_inbound_answered(store):
    rid = await store.record_inbound(InboundWhatsApp("x@s.whatsapp.net", "Ana", "pago?", "", False))
    did = await store.create_draft(rid, "x@s.whatsapp.net", "Ana", "sí, mañana")
    queued = await store.queue_draft(did)
    assert queued is not None and queued.text == "sí, mañana"
    # answered inbound no longer pending
    assert all(r.id != rid for r in await store.list_pending(10))


async def test_queue_draft_twice_returns_none_second_time(store):
    rid = await store.record_inbound(InboundWhatsApp("x@s.whatsapp.net", "Ana", "hi", "", False))
    did = await store.create_draft(rid, "x@s.whatsapp.net", "Ana", "ok")
    assert await store.queue_draft(did) is not None
    assert await store.queue_draft(did) is None  # no double-queue


async def test_claim_queued_is_atomic_no_double_send(store):
    rid = await store.record_inbound(InboundWhatsApp("x@s.whatsapp.net", "Ana", "hi", "", False))
    did = await store.create_draft(rid, "x@s.whatsapp.net", "Ana", "ok")
    await store.queue_draft(did)
    first = await store.claim_queued()
    second = await store.claim_queued()
    assert first is not None and second is None  # only one claim wins


async def test_filter_roundtrip(store):
    await store.add_contact("Juan")
    await store.add_keyword("factura")
    f = await store.get_filter()
    assert "Juan" in f.contacts and "factura" in f.keywords
    await store.remove_contact("Juan")
    assert "Juan" not in (await store.get_filter()).contacts


async def test_claim_queued_deterministic_with_stale_sending_row(store):
    """A pre-existing 'sending' row must not be returned; only the newly queued row wins."""
    # Insert a stale 'sending' row directly (simulates a crashed send)
    await store._c.execute(
        "INSERT INTO whatsapp_messages "
        "(wa_chat_id, contact_name, direction, text, media_kind, status, ts) "
        "VALUES ('stale@s.whatsapp.net', 'Stale', 'out', 'stale msg', '', 'sending', "
        "'2026-01-01T00:00:00')"
    )
    await store._c.commit()

    # Queue a fresh outbound message
    rid = await store.record_inbound(InboundWhatsApp("y@s.whatsapp.net", "Bob", "hey", "", False))
    did = await store.create_draft(rid, "y@s.whatsapp.net", "Bob", "fresh msg")
    await store.queue_draft(did)

    # claim_queued must return the queued row, not the stale sending one
    claimed = await store.claim_queued()
    assert claimed is not None
    assert claimed.text == "fresh msg"
    assert claimed.contact_name == "Bob"

    # A second claim on empty queue returns None
    second = await store.claim_queued()
    assert second is None
