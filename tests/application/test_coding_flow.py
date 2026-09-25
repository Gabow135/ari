from dataclasses import dataclass, replace as dc_replace

from ari.application.coding.flow import route_message, CodingDeps
from ari.application.coding.authorizer import Authorizer
from ari.application.coding.pending_store import PendingStore
from ari.application.coding.request_coding import RequestCoding
from ari.infrastructure.coder.workspace import Workspace
from tests.coding_fakes import FakeCoder


def _deps(tmp_path, scheduled):
    store = PendingStore()
    ws = Workspace(str(tmp_path))
    (tmp_path / "repo").mkdir(exist_ok=True)
    rc = RequestCoding(FakeCoder(plan_summary="1. do it"), ws, store, str(tmp_path / "repo"))
    # confirm_coding now accepts (user_id, action); record the coro and close it
    # immediately so there's no "never awaited" warning.
    def _scheduler(coro):
        scheduled.append(coro)
        coro.close()

    return CodingDeps(
        authorizer=Authorizer({"42"}), pending_store=store, request_coding=rc,
        confirm_coding=lambda uid, action: _fake_confirm(uid, action),
        chat=None,
        scheduler=_scheduler,
    )


async def _fake_confirm(uid, action):
    """Stub coroutine — never executed in these tests."""
    pass


async def test_non_owner_code_is_declined(tmp_path):
    deps = _deps(tmp_path, [])
    reply = await route_message("/code add X", "999", deps)
    assert "solo" in reply.lower() or "no autoriz" in reply.lower()


async def test_owner_code_returns_plan_and_stores(tmp_path):
    deps = _deps(tmp_path, [])
    reply = await route_message("/code add X", "42", deps)
    assert "do it" in reply and deps.pending_store.get("42") is not None


async def test_confirm_without_pending_falls_to_chat(tmp_path):
    called = {}
    deps = _deps(tmp_path, [])
    async def chat(text, user_id): called["hit"] = True; return "chat reply"
    deps.chat = chat
    reply = await route_message("dale", "42", deps)   # no pending
    assert reply == "chat reply" and called.get("hit")


async def test_double_dale_schedules_exactly_one_confirm(tmp_path):
    """Two rapid 'dale' messages must schedule exactly one confirm coroutine.

    The second 'dale' arrives while the first coroutine has been scheduled but
    not yet awaited (i.e., is_busy is True after the first route_message).
    """
    scheduled = []
    deps = _deps(tmp_path, scheduled)

    # Provide a chat stub in case anything falls through.
    async def _chat(text, uid):
        return "chat"
    deps.chat = _chat

    # First: issue a /code command to establish a pending action.
    reply = await route_message("/code add X", "42", deps)
    assert deps.pending_store.get("42") is not None, "Expected pending action after /code"

    # First dale: should pop the pending, mark busy, schedule confirm.
    r1 = await route_message("dale", "42", deps)
    assert "arranco" in r1.lower() or "dale" in r1.lower()

    # Second dale: busy is True now, no pending. Must NOT schedule another confirm.
    # The flow will NOT find pending (already popped), so "dale" falls to chat —
    # but if it somehow found a second pending it would try to schedule again.
    # The key assertion is that scheduled has exactly 1 entry.
    await route_message("dale", "42", deps)

    assert len(scheduled) == 1, (
        f"Expected exactly 1 scheduled confirm, got {len(scheduled)}"
    )
