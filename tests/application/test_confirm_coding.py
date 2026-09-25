from ari.application.coding.confirm_coding import ConfirmCoding
from ari.application.coding.pending_store import PendingStore
from ari.domain.coding.entities import CodingInstruction, CodingPlan, PendingAction
from tests.coding_fakes import FakeCoder


class FakeWorkspace:
    def __init__(self): self.created = []
    async def create_branch(self, target_dir, slug):
        branch = f"ari/tg-{slug}"
        self.created.append((target_dir, branch))
        return branch


def _seed(store, user_id="42"):
    instr = CodingInstruction(user_id, "add healthcheck")
    action = PendingAction(instr, CodingPlan("s", "/repo", instr.text))
    store.put(user_id, action)
    return action


async def test_confirm_executes_and_reports():
    store = PendingStore()
    action = _seed(store)
    # Simulate what route_message now does: pop + mark_busy before scheduling.
    popped = store.pop("42")
    store.mark_busy("42")
    coder = FakeCoder()
    msgs = []
    cc = ConfirmCoding(coder, FakeWorkspace(), store, slug_source=lambda: "1")
    await cc("42", action=popped, report=lambda t: msgs.append(t) or _noop())
    assert coder.executed and coder.executed[0][0] == "ari/tg-1"
    assert any("ari/tg-1" in m for m in msgs)         # start or final names the branch
    assert not store.is_busy("42")                    # busy cleared
    assert store.get("42") is None                    # pending consumed


async def test_confirm_reports_failure_without_crashing():
    store = PendingStore()
    action = _seed(store)
    popped = store.pop("42")
    store.mark_busy("42")
    cc = ConfirmCoding(FakeCoder(fail=True), FakeWorkspace(), store, slug_source=lambda: "1")
    msgs = []
    await cc("42", action=popped, report=lambda t: msgs.append(t) or _noop())
    assert any("falló" in m.lower() or "error" in m.lower() for m in msgs)
    assert not store.is_busy("42")


async def _noop():
    return None
