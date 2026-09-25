from dataclasses import dataclass

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
    return CodingDeps(
        authorizer=Authorizer({"42"}), pending_store=store, request_coding=rc,
        confirm_coding=None, chat=None, scheduler=lambda coro: scheduled.append(coro))


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
