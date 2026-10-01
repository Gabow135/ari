from datetime import datetime, timedelta, timezone

import pytest

from ari.application.credentials.request_runner import CredentialRequestRunner
from ari.domain.credentials.requests import DONE, FAILED, SKIPPED
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_credential_requests import SqliteCredentialRequests


@pytest.fixture
async def requests():
    conn = await connect(":memory:", embedding_dim=4)
    yield SqliteCredentialRequests(conn)
    await conn.close()


class _Vault:
    def __init__(self, url="https://192.168.0.5:8765/v/tok", boom=False):
        self._url, self._boom = url, boom
        self.calls = 0
        self.registered: list[list[str]] = []

    def register_names(self, names):
        self.registered.append(list(names))

    def new_link(self):
        self.calls += 1
        if self._boom:
            raise RuntimeError("cert failed")
        return self._url


def _runner(requests, vault, clock=None):
    sent = []
    async def send(chat_id, text):
        sent.append((chat_id, text))
    if clock is None:
        clock = lambda: datetime.now(timezone.utc)
    return CredentialRequestRunner(requests, vault, send, clock), sent


async def test_pending_request_gets_link(requests):
    rid = await requests.add("42", "42", "el api de groq")
    vault = _Vault()
    runner, sent = _runner(requests, vault)
    await runner()
    assert vault.calls == 1
    assert len(sent) == 1
    chat_id, text = sent[0]
    assert chat_id == "42"
    assert "https://192.168.0.5:8765/v/tok" in text and "el api de groq" in text
    assert await requests.status_of(rid) == DONE


async def test_stale_request_skipped_without_link(requests):
    rid = await requests.add("42", "42", "groq")
    vault = _Vault()
    runner, sent = _runner(requests, vault, clock=lambda: datetime.now(timezone.utc) + timedelta(hours=2))
    await runner()
    assert vault.calls == 0 and sent == []
    assert await requests.status_of(rid) == SKIPPED


async def test_new_link_failure_is_reported(requests):
    rid = await requests.add("42", "42", "groq")
    vault = _Vault(boom=True)
    runner, sent = _runner(requests, vault)
    await runner()
    assert await requests.status_of(rid) == FAILED
    assert len(sent) == 1 and "No pude generar el link" in sent[0][1]


async def test_register_names_called_before_new_link(requests):
    """register_names is called with the parsed var names before new_link is invoked."""
    await requests.add("42", "42", "quiero pasarte NOTION_API_KEY")
    vault = _Vault()
    runner, _ = _runner(requests, vault)
    await runner()
    assert vault.registered == [["NOTION_API_KEY"]]
    assert vault.calls == 1
