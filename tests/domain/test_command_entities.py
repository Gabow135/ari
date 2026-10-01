from datetime import datetime, timezone

import pytest

from ari.domain.command.entities import PendingCommand
from ari.domain.command.requests import PENDING, CommandRequest


def test_pending_command_holds_command_and_defaults_to_proposed():
    pc = PendingCommand("ls -la")
    assert pc.command == "ls -la"
    assert pc.proposed is True


def test_pending_command_rejects_empty():
    with pytest.raises(ValueError):
        PendingCommand("   ")


def test_command_request_is_a_plain_record():
    now = datetime.now(timezone.utc)
    req = CommandRequest(1, "u1", "c1", "pytest -q", now)
    assert (req.id, req.user_id, req.chat_id, req.command, req.created_at) == (
        1, "u1", "c1", "pytest -q", now)
    assert PENDING == "pending"
