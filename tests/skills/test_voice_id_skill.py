import importlib.util
import json
import logging
import pathlib
import time

import numpy as np
import pytest

from ari.domain.skills.models import Attachment, RawInbound


def _mod():
    p = pathlib.Path("skills/voice_id/skill.py")
    spec = importlib.util.spec_from_file_location("voice_id_skill_under_test", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Ctx:
    def __init__(self, secrets=None):
        self.config = {}
        self.log = logging.getLogger("test")
        self._secrets = secrets or {}

    def optional_secret(self, name):
        return self._secrets.get(name)


def _raw_text(text, user_id="1", chat_id="2"):
    return RawInbound(user_id=user_id, chat_id=chat_id, text=text)


def _raw_voice(data=b"fake-audio"):
    return RawInbound(
        user_id="1",
        chat_id="2",
        attachment=Attachment(kind="voice", mime="audio/ogg", data=data),
    )


def _fake_embedder(returns):
    """Return an embedder_factory that produces a fixed embedding."""
    async def embed(audio_bytes, mime):
        return returns

    def factory(token):
        return embed

    return factory


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


# ---- enrollment completion (voice note after /enroll) ------------------------


async def test_voice_note_completes_pending_enrollment(tmp_path):
    emb = np.array([1.0, 0.0, 0.0])
    skill = _mod().VoiceIdSkill(
        {"profiles_dir": str(tmp_path)},
        embedder_factory=_fake_embedder(emb),
    )
    # First the text command
    await skill.on_inbound(_raw_text("/enroll Gabriel"), _Ctx())
    # Then the voice note
    result = await skill.on_inbound(
        _raw_voice(), _Ctx(secrets={"HUGGINGFACE_TOKEN": "fake-token"})
    )
    assert result == "✓ Perfil de Gabriel guardado."
    assert (tmp_path / "Gabriel.npy").exists()
    assert not (tmp_path / "_pending.json").exists()


async def test_enrollment_clears_pending_after_save(tmp_path):
    emb = np.array([0.5, 0.5])
    skill = _mod().VoiceIdSkill(
        {"profiles_dir": str(tmp_path)},
        embedder_factory=_fake_embedder(emb),
    )
    await skill.on_inbound(_raw_text("/enroll Ana"), _Ctx())
    await skill.on_inbound(
        _raw_voice(), _Ctx(secrets={"HUGGINGFACE_TOKEN": "tok"})
    )
    # Second voice note: no longer a pending enrollment
    result = await skill.on_inbound(
        _raw_voice(), _Ctx(secrets={"HUGGINGFACE_TOKEN": "tok"})
    )
    # With profiles but no GROQ key and speaker identified: returns label
    assert result is not None
    assert "Ana" in result


async def test_expired_pending_is_ignored(tmp_path):
    emb = np.array([1.0, 0.0])
    skill = _mod().VoiceIdSkill(
        {"profiles_dir": str(tmp_path), "enroll_timeout": 1},
        embedder_factory=_fake_embedder(emb),
    )
    # Write an already-expired pending file
    expired = {"name": "Old", "chat_id": "2", "ts": time.time() - 10}
    (tmp_path / "_pending.json").write_text(json.dumps(expired))
    # Voice note should skip enrollment (pending expired)
    result = await skill.on_inbound(
        _raw_voice(), _Ctx(secrets={"HUGGINGFACE_TOKEN": "tok"})
    )
    assert not (tmp_path / "Old.npy").exists()
    # No profiles → None
    assert result is None


# ---- speaker identification --------------------------------------------------


async def test_identifies_known_speaker(tmp_path):
    emb = np.array([1.0, 0.0, 0.0], dtype=float)
    np.save(str(tmp_path / "Gabriel.npy"), emb)

    skill = _mod().VoiceIdSkill(
        {"profiles_dir": str(tmp_path), "speaker_id_threshold": 0.9},
        embedder_factory=_fake_embedder(emb),
    )
    result = await skill.on_inbound(
        _raw_voice(), _Ctx(secrets={"HUGGINGFACE_TOKEN": "tok"})
    )
    assert result is not None
    assert "Gabriel" in result


async def test_unknown_speaker_returns_none(tmp_path):
    known_emb = np.array([1.0, 0.0, 0.0], dtype=float)
    np.save(str(tmp_path / "Gabriel.npy"), known_emb)

    # Send a very different embedding (cosine similarity ≈ 0)
    different_emb = np.array([0.0, 1.0, 0.0], dtype=float)
    skill = _mod().VoiceIdSkill(
        {"profiles_dir": str(tmp_path), "speaker_id_threshold": 0.9},
        embedder_factory=_fake_embedder(different_emb),
    )
    result = await skill.on_inbound(
        _raw_voice(), _Ctx(secrets={"HUGGINGFACE_TOKEN": "tok"})
    )
    assert result is None


async def test_identified_speaker_with_groq_key_includes_transcript(tmp_path):
    emb = np.array([1.0, 0.0], dtype=float)
    np.save(str(tmp_path / "Mamá.npy"), emb)

    transcript_text = "¿Cómo estás?"

    async def mock_transcribe(self, att, api_key, ctx):
        return transcript_text

    mod = _mod()
    mod.VoiceIdSkill._transcribe = mock_transcribe

    skill = mod.VoiceIdSkill(
        {"profiles_dir": str(tmp_path), "speaker_id_threshold": 0.5},
        embedder_factory=_fake_embedder(emb),
    )
    result = await skill.on_inbound(
        _raw_voice(),
        _Ctx(secrets={"HUGGINGFACE_TOKEN": "tok", "GROQ_API_KEY": "groq-key"}),
    )
    assert result == f"**Mamá:** {transcript_text}"


# ---- non-enrollment inputs ---------------------------------------------------


async def test_other_text_returns_none(tmp_path):
    skill = _mod().VoiceIdSkill({"profiles_dir": str(tmp_path)})
    assert await skill.on_inbound(_raw_text("hola"), _Ctx()) is None


async def test_voice_without_token_returns_none(tmp_path):
    skill = _mod().VoiceIdSkill({"profiles_dir": str(tmp_path)})
    assert await skill.on_inbound(_raw_voice(), _Ctx()) is None


async def test_voice_no_profiles_returns_none(tmp_path):
    skill = _mod().VoiceIdSkill(
        {"profiles_dir": str(tmp_path)},
        embedder_factory=_fake_embedder(np.array([1.0])),
    )
    result = await skill.on_inbound(
        _raw_voice(), _Ctx(secrets={"HUGGINGFACE_TOKEN": "tok"})
    )
    assert result is None


async def test_none_text_returns_none(tmp_path):
    skill = _mod().VoiceIdSkill({"profiles_dir": str(tmp_path)})
    assert await skill.on_inbound(RawInbound(user_id="1", chat_id="2"), _Ctx()) is None
