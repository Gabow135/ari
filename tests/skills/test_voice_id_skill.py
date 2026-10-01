import importlib.util
import json
import logging
import pathlib
import time

import pytest

from ari.domain.skills.models import Attachment, RawInbound


def _mod():
    p = pathlib.Path("skills/voice_id/skill.py")
    spec = importlib.util.spec_from_file_location("voice_id_skill_under_test", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Ctx:
    def __init__(self):
        self.config = {}
        self.log = logging.getLogger("test")


def _raw_text(text, user_id="1", chat_id="2"):
    return RawInbound(user_id=user_id, chat_id=chat_id, text=text)


def _raw_voice():
    return RawInbound(user_id="1", chat_id="2",
                      attachment=Attachment(kind="voice", mime="audio/ogg", data=b"x"))


# ---- enrollment command ------------------------------------------------------

async def test_enroll_saves_pending_and_returns_prompt(tmp_path):
    skill = _mod().VoiceIdSkill({"profiles_dir": str(tmp_path)})
    result = await skill.on_inbound(_raw_text("/enroll Gabriel"), _Ctx())
    assert result == "Listo. Envía una nota de voz para enrollar a Gabriel."
    pending = json.loads((tmp_path / "_pending.json").read_text())
    assert pending["name"] == "Gabriel"
    assert pending["chat_id"] == "2"


async def test_enroll_strips_at_prefix(tmp_path):
    skill = _mod().VoiceIdSkill({"profiles_dir": str(tmp_path)})
    result = await skill.on_inbound(_raw_text("/enroll @María"), _Ctx())
    assert "María" in result
    pending = json.loads((tmp_path / "_pending.json").read_text())
    assert pending["name"] == "María"


async def test_enroll_missing_name_returns_usage(tmp_path):
    skill = _mod().VoiceIdSkill({"profiles_dir": str(tmp_path)})
    result = await skill.on_inbound(_raw_text("/enroll"), _Ctx())
    assert "Uso:" in result


async def test_enroll_at_only_returns_usage(tmp_path):
    skill = _mod().VoiceIdSkill({"profiles_dir": str(tmp_path)})
    result = await skill.on_inbound(_raw_text("/enroll @"), _Ctx())
    assert "Uso:" in result


# ---- non-enrollment inputs ---------------------------------------------------

async def test_other_text_returns_none(tmp_path):
    skill = _mod().VoiceIdSkill({"profiles_dir": str(tmp_path)})
    assert await skill.on_inbound(_raw_text("hola"), _Ctx()) is None


async def test_voice_attachment_returns_none(tmp_path):
    skill = _mod().VoiceIdSkill({"profiles_dir": str(tmp_path)})
    assert await skill.on_inbound(_raw_voice(), _Ctx()) is None


async def test_none_text_returns_none(tmp_path):
    skill = _mod().VoiceIdSkill({"profiles_dir": str(tmp_path)})
    assert await skill.on_inbound(RawInbound(user_id="1", chat_id="2"), _Ctx()) is None
