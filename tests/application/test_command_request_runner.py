from datetime import datetime, timedelta, timezone

from ari.application.coding.pending_store import PendingStore
from ari.application.command.request_runner import BUSY, STALE, CommandRequestRunner
from ari.domain.command.entities import PendingCommand
from ari.domain.command.requests import DONE, SKIPPED
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_command_requests import SqliteCommandRequests

import pytest


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
async def requests():
    conn = await connect(":memory:", embedding_dim=4)
    yield SqliteCommandRequests(conn)
    await conn.close()


def _runner(requests, pending=None, clock=None):
    sent = []

    async def send(chat_id, text):
        sent.append((chat_id, text))

    runner = CommandRequestRunner(requests, pending or PendingStore(), send,
                                  clock or Clock(datetime.now(timezone.utc)))
    return runner, sent


async def test_proposes_pending_command_and_notifies(requests):
    pending = PendingStore()
    rid = await requests.add("42", "42", "pytest -q")
    runner, sent = _runner(requests, pending=pending)
    await runner()
    action = pending.get("42")
    assert isinstance(action, PendingCommand) and action.command == "pytest -q"
    assert action.proposed is True
    assert sent[0][0] == "42" and "pytest -q" in sent[0][1]
    assert await requests.status_of(rid) == DONE


async def test_stale_request_is_skipped_with_notice(requests):
    rid = await requests.add("42", "42", "rm -rf algo")
    runner, sent = _runner(requests, clock=Clock(datetime.now(timezone.utc) + timedelta(hours=2)))
    await runner()
    assert await requests.status_of(rid) == SKIPPED
    assert sent == [("42", STALE.format("rm -rf algo"))]


async def test_skipped_while_a_confirmed_command_is_executing(requests):
    pending = PendingStore()
    pending.mark_busy("42")   # a confirmed command is running right now
    rid = await requests.add("42", "42", "ls")
    runner, sent = _runner(requests, pending=pending)
    await runner()
    assert await requests.status_of(rid) == SKIPPED
    assert sent == [("42", BUSY.format("ls"))]


async def test_new_command_supersedes_older_unconfirmed_one(requests):
    pending = PendingStore()
    pending.put("42", PendingCommand("comando viejo"))
    await requests.add("42", "42", "comando nuevo")
    runner, _sent = _runner(requests, pending=pending)
    await runner()
    assert pending.get("42").command == "comando nuevo"
