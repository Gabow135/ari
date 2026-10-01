from ari.application.coding.authorizer import Authorizer
from ari.application.coding.flow import CodingDeps, route_message
from ari.application.coding.pending_store import PendingStore
from ari.domain.command.entities import PendingCommand


async def _fake_confirm(uid, action):
    """Stub coroutine — closed by the scheduler, never executed here."""
    pass


def _deps(scheduled, chat=None):
    cmd_store = PendingStore()

    def _scheduler(coro):
        scheduled.append(coro)
        coro.close()

    deps = CodingDeps(
        authorizer=Authorizer({"42"}),
        pending_store=PendingStore(),
        request_coding=None,
        confirm_coding=None,
        chat=chat,
        scheduler=_scheduler,
        command_store=cmd_store,
        confirm_command=lambda uid, action: _fake_confirm(uid, action),
    )
    return deps, cmd_store


async def test_dale_runs_pending_command():
    scheduled = []
    deps, cmd_store = _deps(scheduled)
    cmd_store.put("42", PendingCommand("pytest -q"))
    reply = await route_message("dale", "42", deps)
    assert "corro" in reply.lower() or "dale" in reply.lower()
    assert len(scheduled) == 1
    assert cmd_store.get("42") is None        # consumed
    assert cmd_store.is_busy("42")            # locked while it runs


async def test_no_cancels_pending_command():
    deps, cmd_store = _deps([])
    cmd_store.put("42", PendingCommand("rm -rf algo"))
    reply = await route_message("no", "42", deps)
    assert reply == "Cancelado."
    assert cmd_store.get("42") is None


async def test_generic_affirmative_does_not_run_command_and_falls_to_chat():
    scheduled, called = [], {}

    async def chat(text, uid):
        called["hit"] = True
        return "chat reply"

    deps, cmd_store = _deps(scheduled, chat=chat)
    cmd_store.put("42", PendingCommand("ls"))
    reply = await route_message("sí", "42", deps)
    assert reply == "chat reply" and called.get("hit")
    assert scheduled == []                     # a command only runs on exact «dale»
    assert cmd_store.get("42") is not None      # not consumed


async def test_double_dale_schedules_exactly_one():
    scheduled = []

    async def chat(text, uid):
        return "chat"

    deps, cmd_store = _deps(scheduled, chat=chat)
    cmd_store.put("42", PendingCommand("ls"))
    await route_message("dale", "42", deps)     # pops + busy + schedules
    await route_message("dale", "42", deps)     # busy, nothing pending
    assert len(scheduled) == 1


async def test_no_command_pending_falls_through_to_chat():
    called = {}

    async def chat(text, uid):
        called["hit"] = True
        return "chat reply"

    deps, _cmd_store = _deps([], chat=chat)
    reply = await route_message("hola", "42", deps)
    assert reply == "chat reply" and called.get("hit")
