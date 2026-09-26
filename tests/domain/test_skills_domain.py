import dataclasses
import pytest

from ari.domain.skills.models import Attachment, RawInbound, Delivery, InboundContext
from ari.domain.skills.contracts import InboundTransform, OutboundTransform


def test_models_are_frozen_with_sane_defaults():
    a = Attachment(kind="voice", mime="audio/ogg", data=b"x")
    assert a.filename == "" and a.duration is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.kind = "audio"
    r = RawInbound(user_id="1", chat_id="2")
    assert r.text is None and r.attachment is None
    d = Delivery(kind="voice", data=b"w", mime="audio/wav")
    assert d.text is None


class _DuckSkill:
    async def on_inbound(self, raw, ctx):
        return "hi"

    async def on_outbound(self, reply, origin, ctx):
        return None


def test_protocols_match_a_duck_typed_skill():
    s = _DuckSkill()
    assert isinstance(s, InboundTransform)
    assert isinstance(s, OutboundTransform)
