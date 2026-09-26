import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from ari.application.coding.pending_store import PendingStore
from ari.application.coding.request_runner import CodingRequestRunner
from ari.domain.coding.requests import DONE, FAILED, SKIPPED
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_coding_requests import SqliteCodingRequests


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
async def requests():
    conn = await connect(":memory:", embedding_dim=4)
    yield SqliteCodingRequests(conn)
    await conn.close()


def _runner(requests, request_coding, pending=None, clock=None):
    sent, tasks = [], []

    async def send(chat_id, text):
        sent.append((chat_id, text))

    def spawn(coro):
        tasks.append(asyncio.ensure_future(coro))

    runner = CodingRequestRunner(requests, request_coding, pending or PendingStore(), send,
                                 spawn, clock or Clock(datetime.now(timezone.utc)))
    return runner, sent, tasks


async def test_plans_in_background_and_sends_the_reply(requests):
    calls = []

    async def request_coding(user_id, text, target):
        calls.append((user_id, text, target))
        return "Plan para `x`:\n1. hacer\n\nResponde *dale* para ejecutar, o *no* para cancelar."

    rid = await requests.add("42", "42", "agrega /ping", None)
    runner, sent, tasks = _runner(requests, request_coding)
    await runner()
    await asyncio.gather(*tasks)
    assert calls == [("42", "agrega /ping", None)]
    assert sent[0][0] == "42" and sent[0][1].startswith("Plan para")
    assert await requests.status_of(rid) == DONE


async def test_runner_does_not_block_while_planning(requests):
    release = asyncio.Event()

    async def slow(user_id, text, target):
        await release.wait()
        return "plan"

    await requests.add("42", "42", "x", None)
    runner, sent, tasks = _runner(requests, slow)
    await asyncio.wait_for(runner(), timeout=1)
    assert sent == []
    release.set()
    await asyncio.gather(*tasks)
    assert sent == [("42", "plan")]


async def test_second_proposal_skipped_while_one_is_planning(requests):
    release = asyncio.Event()

    async def slow(user_id, text, target):
        await release.wait()
        return "plan"

    first = await requests.add("42", "42", "uno", None)
    second = await requests.add("42", "42", "dos", None)
    runner, sent, tasks = _runner(requests, slow)
    await runner()
    assert sent == [("42", "Ya tengo un trabajo o plan de código pendiente; respóndelo primero.")]
    assert await requests.status_of(second) == SKIPPED
    release.set()
    await asyncio.gather(*tasks)
    assert await requests.status_of(first) == DONE


async def test_skipped_when_a_plan_awaits_dale_or_a_job_runs(requests):
    async def never(*a):
        raise AssertionError("must not plan")

    pending = PendingStore()
    pending.mark_busy("42")
    rid = await requests.add("42", "42", "x", None)
    runner, sent, _ = _runner(requests, never, pending=pending)
    await runner()
    assert await requests.status_of(rid) == SKIPPED
    assert "pendiente" in sent[0][1]


async def test_stale_request_is_skipped_with_notice(requests):
    async def never(*a):
        raise AssertionError("must not plan")

    rid = await requests.add("42", "42", "vieja idea", None)
    runner, sent, _ = _runner(requests, never,
                              clock=Clock(datetime.now(timezone.utc) + timedelta(hours=2)))
    await runner()
    assert await requests.status_of(rid) == SKIPPED
    assert sent == [("42", "Descarté una propuesta de código vieja: vieja idea")]


async def test_planning_failure_is_reported_and_others_still_run(requests):
    async def request_coding(user_id, text, target):
        if user_id == "42":
            raise RuntimeError("claude caído")
        return "plan otro"

    bad = await requests.add("42", "42", "x", None)
    good = await requests.add("43", "43", "y", None)
    runner, sent, tasks = _runner(requests, request_coding)
    await runner()
    await asyncio.gather(*tasks)
    assert await requests.status_of(bad) == FAILED
    assert await requests.status_of(good) == DONE
    assert ("42", "No pude preparar el plan: claude caído") in sent
    assert ("43", "plan otro") in sent
