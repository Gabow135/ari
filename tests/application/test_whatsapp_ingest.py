from ari.application.whatsapp.filter import WhatsAppFilter
from ari.application.whatsapp.ingest import WhatsAppIngest
from ari.domain.whatsapp.entities import InboundWhatsApp


class FakeStore:
    def __init__(self, f):
        self._f = f
        self.recorded, self.notified = [], []
    async def record_inbound(self, m):
        self.recorded.append(m)
        return len(self.recorded)
    async def get_filter(self):
        return self._f
    async def mark_notified(self, id):
        self.notified.append(id)


def _ingest(store, sent):
    async def notify(chat_id, text):
        sent.append((chat_id, text))
    return WhatsAppIngest(store, notify, owner_ids=["111", "222"])


async def test_passing_message_notifies_every_owner_and_marks_notified():
    sent = []
    store = FakeStore(WhatsAppFilter(frozenset({"juan"}), frozenset()))
    await _ingest(store, sent)(InboundWhatsApp("5@s.whatsapp.net", "Juan", "hola"))
    assert [c for c, _ in sent] == ["111", "222"]
    assert "Juan" in sent[0][1] and "hola" in sent[0][1]
    assert store.notified == [1]


async def test_non_matching_message_is_stored_but_silent():
    sent = []
    store = FakeStore(WhatsAppFilter(frozenset(), frozenset()))
    await _ingest(store, sent)(InboundWhatsApp("5@s.whatsapp.net", "Ana", "hola"))
    assert sent == [] and store.notified == [] and len(store.recorded) == 1


async def test_media_only_message_uses_placeholder_body():
    sent = []
    store = FakeStore(WhatsAppFilter(frozenset({"ana"}), frozenset()))
    await _ingest(store, sent)(InboundWhatsApp("5@s.whatsapp.net", "Ana", "", media_kind="image"))
    assert "[image]" in sent[0][1]  # no empty body, no crash
