import os

from ari.application.coding.request_coding import RequestCoding
from ari.application.coding.pending_store import PendingStore
from ari.infrastructure.coder.workspace import Workspace
from tests.coding_fakes import FakeCoder


async def test_request_plans_and_stores_pending(tmp_path):
    (tmp_path / "repo").mkdir()
    store = PendingStore()
    rc = RequestCoding(FakeCoder(plan_summary="1. add endpoint"),
                       Workspace(str(tmp_path)), store, default_dir=str(tmp_path / "repo"))
    reply = await rc("42", "add healthcheck", target=None)
    assert "add endpoint" in reply
    assert "dale" in reply.lower()
    assert store.get("42") is not None


async def test_request_refuses_bad_target(tmp_path):
    (tmp_path / "repo").mkdir()
    store = PendingStore()
    rc = RequestCoding(FakeCoder(), Workspace(str(tmp_path / "repo")), store,
                       default_dir=str(tmp_path / "repo"))
    reply = await rc("42", "do X", target="../etc")
    assert "no puedo" in reply.lower() or "fuera" in reply.lower()
    assert store.get("42") is None      # nothing stored on refusal
