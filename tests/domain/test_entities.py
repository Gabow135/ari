# tests/domain/test_entities.py
from datetime import datetime, timezone

import pytest

from ari.domain.agent.message import Message
from ari.domain.agent.conversation import Conversation


def _msg(content="hi", role="user"):
    return Message(user_id="u1", role=role, content=content,
                   created_at=datetime.now(timezone.utc))


def test_message_rejects_empty_content():
    with pytest.raises(ValueError):
        _msg(content="   ")


def test_conversation_last_returns_tail_in_order():
    convo = Conversation(user_id="u1", messages=[])
    for i in range(5):
        convo.add(_msg(content=f"m{i}"))
    tail = convo.last(3)
    assert [m.content for m in tail] == ["m2", "m3", "m4"]
