"""End-to-end (fast, no real CLI): a proponer_codigo-style queued request goes
through the real CodingRequestRunner + RequestCoding, and the owner's «dale»
reaches ConfirmCoding and actually calls the stub coder's execute — proving the
whole proactive-code wire-up (runner -> PendingStore -> route_message ->
ConfirmCoding) works together, not just each piece in isolation."""
import asyncio
from datetime import datetime, timezone

from ari.application.coding.authorizer import Authorizer
from ari.application.coding.confirm_coding import ConfirmCoding
from ari.application.coding.flow import CodingDeps, route_message
from ari.application.coding.pending_store import PendingStore
from ari.application.coding.request_coding import RequestCoding
from ari.application.coding.request_runner import CodingRequestRunner
from ari.infrastructure.coder.workspace import Workspace
from ari.infrastructure.persistence.db import connect
from ari.infrastructure.persistence.sqlite_coding_requests import SqliteCodingRequests
from tests.coding_fakes import FakeCoder


class _FakeWorkspace:
    """Stand-in for ConfirmCoding's branch creation: real Workspace.create_branch
    shells out to git, which this integration test doesn't need to exercise."""
    async def create_branch(self, target_dir, slug):
        return f"ari/tg-{slug}"


async def test_proposed_request_runs_then_dale_reaches_confirm_coding(tmp_path):
    conn = await connect(str(tmp_path / "ari.db"), embedding_dim=4)
    try:
        requests = SqliteCodingRequests(conn)
        store = PendingStore()
        ws = Workspace(str(tmp_path))
        (tmp_path / "repo").mkdir()
        coder = FakeCoder(plan_summary="1. add endpoint")
        request_coding = RequestCoding(coder, ws, store, default_dir=str(tmp_path / "repo"))
        confirm = ConfirmCoding(coder, _FakeWorkspace(), store, slug_source=lambda: "1")

        sent = []

        async def send(chat_id, text):
            sent.append((chat_id, text))

        runner_tasks = []

        def spawn(coro):
            runner_tasks.append(asyncio.ensure_future(coro))

        runner = CodingRequestRunner(requests, request_coding, store, send, spawn,
                                     lambda: datetime.now(timezone.utc))

        await requests.add("42", "42", "agrega un endpoint", None)
        await runner()
        await asyncio.gather(*runner_tasks)

        # The runner planned in the background and stored a *proposed* PendingAction.
        pending = store.get("42")
        assert pending is not None and pending.proposed is True
        assert any("add endpoint" in text for _chat, text in sent)

        # Now the owner confirms with the exact "dale" through the normal chat flow.
        confirm_tasks = []

        def scheduler(coro):
            confirm_tasks.append(asyncio.ensure_future(coro))

        reports = []

        async def report(text):
            reports.append(text)

        async def confirm_coding(uid, action):
            await confirm(uid, action, report)

        deps = CodingDeps(
            authorizer=Authorizer({"42"}), pending_store=store, request_coding=request_coding,
            confirm_coding=confirm_coding, chat=None, scheduler=scheduler,
        )
        reply = await route_message("dale", "42", deps)
        assert "arranco" in reply.lower()
        assert len(confirm_tasks) == 1
        await asyncio.gather(*confirm_tasks)

        # ConfirmCoding really ran the stub coder's execute.
        assert coder.executed and coder.executed[0][0] == "ari/tg-1"
        assert store.get("42") is None
        assert not store.is_busy("42")
    finally:
        await conn.close()


async def test_generic_affirmative_does_not_execute_a_proposed_plan(tmp_path):
    """Sanity check inside the same wiring: "sí" must not trigger execute()."""
    conn = await connect(str(tmp_path / "ari.db"), embedding_dim=4)
    try:
        requests = SqliteCodingRequests(conn)
        store = PendingStore()
        ws = Workspace(str(tmp_path))
        (tmp_path / "repo").mkdir()
        coder = FakeCoder(plan_summary="1. add endpoint")
        request_coding = RequestCoding(coder, ws, store, default_dir=str(tmp_path / "repo"))

        async def send(chat_id, text):
            pass

        tasks = []

        def spawn(coro):
            tasks.append(asyncio.ensure_future(coro))

        runner = CodingRequestRunner(requests, request_coding, store, send, spawn,
                                     lambda: datetime.now(timezone.utc))
        await requests.add("42", "42", "agrega un endpoint", None)
        await runner()
        await asyncio.gather(*tasks)

        async def chat(text, uid):
            return "chat reply"

        deps = CodingDeps(
            authorizer=Authorizer({"42"}), pending_store=store, request_coding=request_coding,
            confirm_coding=lambda uid, action: _never_called(), chat=chat, scheduler=lambda c: c.close(),
        )
        reply = await route_message("sí", "42", deps)
        assert reply == "chat reply"
        assert not coder.executed
        assert store.get("42") is not None
    finally:
        await conn.close()


async def _never_called():
    raise AssertionError("confirm_coding must not run for a generic affirmative")
