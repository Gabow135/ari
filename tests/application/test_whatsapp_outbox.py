from ari.application.whatsapp.outbox import WhatsAppOutbox
from ari.infrastructure.persistence.sqlite_whatsapp import Row


class FakeStore:
    def __init__(self, rows):
        self._rows = list(rows)
        self.sent, self.failed = [], []
    async def claim_queued(self):
        return self._rows.pop(0) if self._rows else None
    async def mark_sent(self, id):
        self.sent.append(id)
    async def mark_failed(self, id):
        self.failed.append(id)


class FakePort:
    def __init__(self, fail=False):
        self.fail, self.sends = fail, []
    async def send(self, wa_chat_id, text):
        if self.fail:
            raise RuntimeError("disconnected")
        self.sends.append((wa_chat_id, text))


def _row(id):
    return Row(id, "x@s.whatsapp.net", "Ana", "hola", "", "t", None)


async def _noop_sleep(_):
    return None


async def test_drains_and_marks_sent():
    store = FakeStore([_row(1), _row(2)])
    port = FakePort()
    sent = []
    async def notify(c, t):
        sent.append((c, t))
    await WhatsAppOutbox(store, port, notify, ["111"], min_delay=0, sleep=_noop_sleep)()
    assert port.sends == [("x@s.whatsapp.net", "hola"), ("x@s.whatsapp.net", "hola")]
    assert store.sent == [1, 2] and store.failed == [] and sent == []


async def test_send_failure_marks_failed_and_notifies_owner():
    store = FakeStore([_row(7)])
    sent = []
    async def notify(c, t):
        sent.append((c, t))
    port = FakePort(fail=True)
    await WhatsAppOutbox(store, port, notify, ["111"], min_delay=0, sleep=_noop_sleep)()
    assert store.failed == [7] and store.sent == []
    assert sent and "Ana" in sent[0][1]


class FakePortFailOnce:
    """FakePort that fails on the first send, then succeeds."""
    def __init__(self):
        self.sends = []
        self._call_count = 0

    async def send(self, wa_chat_id, text):
        self._call_count += 1
        if self._call_count == 1:
            raise RuntimeError("disconnected")
        self.sends.append((wa_chat_id, text))


async def test_drain_continues_past_failed_send():
    """Verify that drain continues after one row fails; first marked failed, second marked sent."""
    store = FakeStore([_row(1), _row(2)])
    port = FakePortFailOnce()
    notifications = []
    async def notify(c, t):
        notifications.append((c, t))
    await WhatsAppOutbox(store, port, notify, ["111"], min_delay=0, sleep=_noop_sleep)()
    # First row failed, second row succeeded
    assert store.failed == [1], f"Expected failed=[1], got {store.failed}"
    assert store.sent == [2], f"Expected sent=[2], got {store.sent}"
    # Port should show only the successful send (failed one never made it to sends list)
    assert port.sends == [("x@s.whatsapp.net", "hola")], f"Expected one send, got {port.sends}"
    # Owner should be notified about the failure
    assert len(notifications) == 1 and "Ana" in notifications[0][1]
