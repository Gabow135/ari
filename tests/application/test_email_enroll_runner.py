from ari.application.email.enroll_runner import EmailEnrollRunner


class FakeRequests:
    def __init__(self, pending): self._p, self.finished = pending, []
    async def claim_pending(self): p, self._p = self._p, []; return p
    async def finish(self, rid): self.finished.append(rid)


class Req:
    def __init__(self, rid, chat): self.id, self.chat_id, self.user_id = rid, chat, chat


class FakeBox:
    def public_key_b64(self): return "PUB=="


class FakeBrokenBox:
    def public_key_b64(self): raise RuntimeError("ARI_VAULT_KEY no está configurada")


async def _noop_send(chat_id, text):
    pass


async def test_runner_renders_and_sends_document():
    reqs = FakeRequests([Req(1, "7")])
    sent = []

    async def send_document(chat_id, filename, content):
        sent.append((chat_id, filename, content))

    runner = EmailEnrollRunner(reqs, FakeBox(), lambda pub: f"<html>{pub}</html>",
                               send_document, _noop_send)
    await runner()
    assert sent[0][0] == "7"
    assert sent[0][1].endswith(".html")
    assert b"PUB==" in sent[0][2]
    assert reqs.finished == [1]


async def test_runner_notifies_user_and_finishes_on_failure():
    """When minting the document fails, the user gets a text message and request is finished."""
    reqs = FakeRequests([Req(2, "9")])
    sent_texts = []

    async def send_document(chat_id, filename, content):
        raise RuntimeError("ARI_VAULT_KEY no está configurada")

    async def send_text(chat_id, text):
        sent_texts.append((chat_id, text))

    runner = EmailEnrollRunner(reqs, FakeBox(), lambda pub: f"<html>{pub}</html>",
                               send_document, send_text)
    await runner()
    assert reqs.finished == [2]
    assert len(sent_texts) == 1
    assert sent_texts[0][0] == "9"
    assert sent_texts[0][1]  # non-empty message
