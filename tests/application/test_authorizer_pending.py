from ari.application.coding.authorizer import Authorizer
from ari.application.coding.pending_store import PendingStore
from ari.domain.coding.entities import CodingInstruction, CodingPlan, PendingAction


def test_authorizer_owner_only():
    a = Authorizer({"42", "7"})
    assert a.is_owner("42")
    assert not a.is_owner("999")


def _pending():
    instr = CodingInstruction("u1", "x")
    return PendingAction(instr, CodingPlan("s", "/r", "x"))


def test_pending_store_lifecycle_and_busy_guard():
    s = PendingStore()
    assert s.get("u1") is None
    s.put("u1", _pending())
    assert s.get("u1") is not None
    assert s.pop("u1") is not None
    assert s.get("u1") is None      # pop removed it

    assert not s.is_busy("u1")
    s.mark_busy("u1")
    assert s.is_busy("u1")
    s.clear_busy("u1")
    assert not s.is_busy("u1")
