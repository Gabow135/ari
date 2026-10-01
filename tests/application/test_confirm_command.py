from ari.application.coding.pending_store import PendingStore
from ari.application.command.confirm_command import ConfirmCommand
from ari.domain.command.entities import CommandResult, PendingCommand


def _confirm(run, store=None, cwd="/tmp"):
    store = store or PendingStore()
    store.mark_busy("42")
    sent = []

    async def report(text):
        sent.append(text)

    return ConfirmCommand(run, store, cwd), store, sent, report


async def test_runs_command_and_reports_output():
    seen = {}

    async def run(command, cwd):
        seen["command"], seen["cwd"] = command, cwd
        return CommandResult(returncode=0, stdout="hola\n")

    confirm, store, sent, report = _confirm(run)
    await confirm("42", PendingCommand("echo hola"), report)

    assert seen == {"command": "echo hola", "cwd": "/tmp"}
    assert "echo hola" in sent[0] and "hola" in sent[0]
    assert not store.is_busy("42")          # busy always cleared


async def test_nonzero_exit_is_shown():
    async def run(command, cwd):
        return CommandResult(returncode=3, stderr="boom")

    confirm, _store, sent, report = _confirm(run)
    await confirm("42", PendingCommand("false"), report)
    assert "3" in sent[0] and "boom" in sent[0]


async def test_timeout_is_reported():
    async def run(command, cwd):
        return CommandResult(returncode=-1, timed_out=True)

    confirm, _store, sent, report = _confirm(run)
    await confirm("42", PendingCommand("sleep 999"), report)
    assert "límite" in sent[0] or "tiempo" in sent[0]


async def test_failure_is_reported_without_crashing_and_clears_busy():
    async def run(command, cwd):
        raise RuntimeError("no pude")

    confirm, store, sent, report = _confirm(run)
    await confirm("42", PendingCommand("x"), report)
    assert "Error" in sent[0]
    assert not store.is_busy("42")
