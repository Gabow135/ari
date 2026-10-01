from ari.application.coding.confirm_coding import ConfirmCoding
from ari.application.coding.pending_store import PendingStore
from ari.domain.coding.entities import CodingInstruction, CodingPlan, PendingAction
from ari.infrastructure.coder.verifier import VerifyResult
from tests.coding_fakes import FakeCoder


class FakeWorkspace:
    def __init__(self):
        self.created = []
        self.merged = []
        self.checked_out = []

    async def current_branch(self, target_dir):
        return "main"

    async def create_branch(self, target_dir, slug):
        branch = f"ari/tg-{slug}"
        self.created.append((target_dir, branch))
        return branch

    async def merge_into(self, target_dir, base, branch):
        self.merged.append((target_dir, base, branch))

    async def checkout(self, target_dir, branch):
        self.checked_out.append((target_dir, branch))


class FakeVerifier:
    def __init__(self, ok=True, detail=""):
        self.calls = []
        self._ok = ok
        self._detail = detail

    async def verify(self, target_dir, deps_changed):
        self.calls.append({"target_dir": target_dir, "deps_changed": deps_changed})
        return VerifyResult(ok=self._ok, detail=self._detail)


def _seed(store, user_id="42"):
    instr = CodingInstruction(user_id, "add healthcheck")
    action = PendingAction(instr, CodingPlan("s", "/repo", instr.text))
    store.put(user_id, action)


async def test_confirm_executes_and_reports():
    store = PendingStore()
    _seed(store)
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
    _seed(store)
    popped = store.pop("42")
    store.mark_busy("42")
    cc = ConfirmCoding(FakeCoder(fail=True), FakeWorkspace(), store, slug_source=lambda: "1")
    msgs = []
    await cc("42", action=popped, report=lambda t: msgs.append(t) or _noop())
    assert any("falló" in m.lower() or "error" in m.lower() for m in msgs)
    assert not store.is_busy("42")


async def test_green_merges_reloads_and_reports():
    """verifier ok + on_merged returns skills → merge recorded, report contains key phrases."""
    store = PendingStore()
    _seed(store)
    popped = store.pop("42")
    store.mark_busy("42")

    ws = FakeWorkspace()
    coder = FakeCoder()
    verifier = FakeVerifier(ok=True)
    reloaded_skills = ["voice_id"]

    async def on_merged():
        return reloaded_skills

    msgs = []
    cc = ConfirmCoding(coder, ws, store, slug_source=lambda: "1",
                       verifier=verifier, on_merged=on_merged)
    await cc("42", action=popped, report=lambda t: msgs.append(t) or _noop())

    # merge was recorded with correct args
    assert ws.merged == [("/repo", "main", "ari/tg-1")]
    # report contains merge and skill info
    combined = " ".join(msgs)
    assert "Mergeado a `main`" in combined
    assert "voice_id" in combined
    # housekeeping
    assert not store.is_busy("42")
    assert store.get("42") is None


async def test_red_does_not_merge_and_leaves_branch():
    """verifier fails → no merge, checkout back to base, report mentions rojo and branch."""
    store = PendingStore()
    _seed(store)
    popped = store.pop("42")
    store.mark_busy("42")

    ws = FakeWorkspace()
    coder = FakeCoder()
    verifier = FakeVerifier(ok=False, detail="3 failed")

    msgs = []
    cc = ConfirmCoding(coder, ws, store, slug_source=lambda: "1",
                       verifier=verifier)
    await cc("42", action=popped, report=lambda t: msgs.append(t) or _noop())

    assert ws.merged == []
    assert ws.checked_out == [("/repo", "main")]
    combined = " ".join(msgs)
    assert "rojo" in combined.lower()
    assert "ari/tg-1" in combined
    assert not store.is_busy("42")


async def test_deps_change_sets_install_flag():
    """FakeCoder with pyproject.toml in changed_files → deps_changed=True passed to verifier."""
    store = PendingStore()
    _seed(store)
    popped = store.pop("42")
    store.mark_busy("42")

    ws = FakeWorkspace()
    verifier = FakeVerifier(ok=True)
    coder_deps = FakeCoder(changed_files=["src/foo.py", "pyproject.toml"])

    msgs = []
    cc = ConfirmCoding(coder_deps, ws, store, slug_source=lambda: "1", verifier=verifier)
    await cc("42", action=popped, report=lambda t: msgs.append(t) or _noop())

    assert verifier.calls[0]["deps_changed"] is True

    # Now with no deps file → deps_changed=False
    store2 = PendingStore()
    _seed(store2, user_id="99")
    popped2 = store2.pop("99")
    store2.mark_busy("99")

    ws2 = FakeWorkspace()
    verifier2 = FakeVerifier(ok=True)
    coder_no_deps = FakeCoder(changed_files=["src/bar.py"])

    cc2 = ConfirmCoding(coder_no_deps, ws2, store2, slug_source=lambda: "1", verifier=verifier2)
    await cc2("99", action=popped2, report=lambda t: msgs.append(t) or _noop())

    assert verifier2.calls[0]["deps_changed"] is False


async def test_no_verifier_preserves_old_behavior():
    """Without verifier the old 'push/PR/merge' message path is preserved."""
    store = PendingStore()
    _seed(store)
    popped = store.pop("42")
    store.mark_busy("42")

    msgs = []
    cc = ConfirmCoding(FakeCoder(), FakeWorkspace(), store, slug_source=lambda: "1")
    await cc("42", action=popped, report=lambda t: msgs.append(t) or _noop())

    assert any("push/PR/merge" in m for m in msgs)
    assert not store.is_busy("42")


async def _noop():
    return None
