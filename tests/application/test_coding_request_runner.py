import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from ari.application.coding.pending_store import PendingStore
from ari.application.coding.request_runner import BUSY, CodingRequestRunner
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

    async def request_coding(user_id, text, target, proposed=False):
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

    async def slow(user_id, text, target, proposed=False):
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

    async def slow(user_id, text, target, proposed=False):
        await release.wait()
        return "plan"

    first = await requests.add("42", "42", "uno", None)
    second = await requests.add("42", "42", "dos", None)
    runner, sent, tasks = _runner(requests, slow)
    await runner()
    assert sent == [("42", BUSY.format("dos"))]
    assert await requests.status_of(second) == SKIPPED
    release.set()
    await asyncio.gather(*tasks)
    assert await requests.status_of(first) == DONE


async def test_skipped_when_a_plan_awaits_dale_or_a_job_runs(requests):
    async def never(*a, **k):
        raise AssertionError("must not plan")

    pending = PendingStore()
    pending.mark_busy("42")
    rid = await requests.add("42", "42", "x", None)
    runner, sent, _ = _runner(requests, never, pending=pending)
    await runner()
    assert await requests.status_of(rid) == SKIPPED
    assert "pendiente" in sent[0][1]


async def test_stale_request_is_skipped_with_notice(requests):
    async def never(*a, **k):
        raise AssertionError("must not plan")

    rid = await requests.add("42", "42", "vieja idea", None)
    runner, sent, _ = _runner(requests, never,
                              clock=Clock(datetime.now(timezone.utc) + timedelta(hours=2)))
    await runner()
    assert await requests.status_of(rid) == SKIPPED
    assert sent == [("42", "Descarté una propuesta de código vieja: vieja idea")]


async def test_planning_failure_is_reported_and_others_still_run(requests):
    async def request_coding(user_id, text, target, proposed=False):
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


async def test_finish_raises_still_sends_reply_and_does_not_raise(requests):
    """If finish() raises on success path, owner still gets plan and task doesn't raise."""
    async def request_coding(user_id, text, target, proposed=False):
        return "plan para hacer"

    class FailingRequests:
        async def claim_pending(self):
            return await requests.claim_pending()

        async def finish(self, req_id, status, reason=None):
            raise RuntimeError("sqlite locked")

    await requests.add("42", "42", "agrega foo", None)
    failing_requests = FailingRequests()
    runner, sent, tasks = _runner(failing_requests, request_coding)
    await runner()
    # Task should not raise even though finish failed
    await asyncio.gather(*tasks)
    # But owner should still get the plan reply
    assert ("42", "plan para hacer") in sent


async def test_send_raises_on_success_does_not_raise(requests):
    """If send() raises on success path, awaiting task doesn't raise."""
    async def request_coding(user_id, text, target, proposed=False):
        return "plan para hacer"

    async def failing_send(chat_id, text):
        raise RuntimeError("http error")

    def spawn_with_failing_send(coro):
        tasks.append(asyncio.ensure_future(coro))

    rid = await requests.add("42", "42", "agrega foo", None)
    runner = CodingRequestRunner(requests, request_coding, PendingStore(), failing_send,
                                 spawn_with_failing_send, Clock(datetime.now(timezone.utc)))
    tasks = []
    await runner()
    # Task should not raise even though send failed
    await asyncio.gather(*tasks)
    # Request should still be marked DONE in DB
    assert await requests.status_of(rid) == DONE


async def test_finish_raises_on_failure_path_still_sends_error(requests):
    """If finish(FAILED) raises, owner still gets error message and task doesn't raise."""
    async def request_coding(user_id, text, target, proposed=False):
        raise RuntimeError("planning failed")

    class FailingRequests:
        async def claim_pending(self):
            return await requests.claim_pending()

        async def finish(self, req_id, status, reason=None):
            raise RuntimeError("sqlite locked")

    await requests.add("42", "42", "agrega foo", None)
    failing_requests = FailingRequests()
    runner, sent, tasks = _runner(failing_requests, request_coding)
    await runner()
    # Task should not raise
    await asyncio.gather(*tasks)
    # But owner should still get error message
    assert ("42", "No pude preparar el plan: planning failed") in sent


async def test_send_raises_on_failure_path_does_not_raise(requests):
    """If send() raises on failure path, awaiting task doesn't raise."""
    async def request_coding(user_id, text, target, proposed=False):
        raise RuntimeError("planning error")

    async def failing_send(chat_id, text):
        raise RuntimeError("http error")

    def spawn_with_failing_send(coro):
        tasks.append(asyncio.ensure_future(coro))

    rid = await requests.add("42", "42", "agrega foo", None)
    runner = CodingRequestRunner(requests, request_coding, PendingStore(), failing_send,
                                 spawn_with_failing_send, Clock(datetime.now(timezone.utc)))
    tasks = []
    await runner()
    # Task should not raise even though send failed
    await asyncio.gather(*tasks)
    # Request should still be marked FAILED in DB
    assert await requests.status_of(rid) == FAILED


async def test_runner_respects_planning_marker_set_elsewhere(requests):
    """The planning marker now lives in PendingStore (shared with route_message's
    /code branch), not a private set on the runner — so anyone marking it busy
    must make the runner skip too."""
    async def never(*a, **k):
        raise AssertionError("must not plan")

    pending = PendingStore()
    pending.mark_planning("42")   # e.g. set by another component sharing the store
    rid = await requests.add("42", "42", "x", None)
    runner, sent, _ = _runner(requests, never, pending=pending)
    await runner()
    assert await requests.status_of(rid) == SKIPPED
    assert sent == [("42", BUSY.format("x"))]


async def test_runner_clears_planning_marker_after_success(requests):
    async def request_coding(user_id, text, target, proposed=False):
        return "plan"

    pending = PendingStore()
    await requests.add("42", "42", "x", None)
    runner, _sent, tasks = _runner(requests, request_coding, pending=pending)
    await runner()
    assert pending.is_planning("42")     # marked while the background task runs
    await asyncio.gather(*tasks)
    assert not pending.is_planning("42")  # cleared once done


async def test_cancelled_while_planning_marks_request_failed_and_reraises(requests):
    """A CancelledError while planning (e.g. process shutdown) must mark the
    row failed (best-effort) and re-raise — never swallow the cancellation."""
    started = asyncio.Event()

    async def hangs(user_id, text, target, proposed=False):
        started.set()
        await asyncio.Event().wait()  # blocks forever until cancelled

    pending = PendingStore()
    rid = await requests.add("42", "42", "x", None)
    runner, _sent, tasks = _runner(requests, hangs, pending=pending)
    await runner()
    await started.wait()
    assert len(tasks) == 1
    task = tasks[0]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await requests.status_of(rid) == FAILED
    assert not pending.is_planning("42")


async def test_stale_notice_truncates_long_instruction(requests):
    long = "x" * 500
    await requests.add("42", "42", long, None)
    sent = []

    async def send(chat_id, text):
        sent.append(text)

    runner = CodingRequestRunner(requests, None, PendingStore(), send, lambda c: None,
                                 lambda: datetime.now(timezone.utc) + timedelta(hours=2))
    await runner()
    assert len(sent[0]) < 200 and sent[0].endswith("…")
