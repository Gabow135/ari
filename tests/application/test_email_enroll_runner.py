import pytest

from ari.application.email.enroll_runner import EmailEnrollRunner


class FakeRequests:
    def __init__(self, pending): self._p, self.finished = pending, []
    async def claim_pending(self): p, self._p = self._p, []; return p
    async def finish(self, rid): self.finished.append(rid)


class Req:
    def __init__(self, rid, chat): self.id, self.chat_id, self.user_id = rid, chat, chat


class FakeBox:
    def public_key_b64(self): return "PUB=="


async def test_runner_renders_and_sends_document():
    reqs = FakeRequests([Req(1, "7")])
    sent = []
    async def send_document(chat_id, filename, content):
        sent.append((chat_id, filename, content))
    runner = EmailEnrollRunner(reqs, FakeBox(), lambda pub: f"<html>{pub}</html>", send_document)
    await runner()
    assert sent[0][0] == "7"
    assert sent[0][1].endswith(".html")
    assert b"PUB==" in sent[0][2]
    assert reqs.finished == [1]
