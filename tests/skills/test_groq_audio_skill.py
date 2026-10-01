import importlib.util
import json
import logging
import pathlib
import time

import httpx
import numpy as np
import pytest

from ari.domain.skills.models import Attachment, InboundContext, RawInbound
from ari.domain.ports.gateway_port import OutgoingMessage


def _mod():
    p = pathlib.Path("skills/groq_audio/skill.py")
    spec = importlib.util.spec_from_file_location("groq_audio_skill_under_test", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Ctx:
    def __init__(self, key="k", hf=None):
        self._key = key
        self._hf = hf
        self.config = {}
        self.log = logging.getLogger("test")

    def secret(self, name):
        return self._key

    def optional_secret(self, name):
        return self._hf


async def test_transcribe_posts_to_groq_stt():
    def handler(request):
        assert request.url.path == "/openai/v1/audio/transcriptions"
        assert request.headers["authorization"] == "Bearer k"
        return httpx.Response(200, json={"text": "hola"})
    client = _mod().GroqClient("k", transport=httpx.MockTransport(handler))
    assert await client.transcribe(b"\x00\x01", model="whisper-large-v3-turbo") == "hola"


async def test_speak_returns_audio_bytes():
    def handler(request):
        assert request.url.path == "/openai/v1/audio/speech"
        return httpx.Response(200, content=b"WAVDATA")
    client = _mod().GroqClient("k", transport=httpx.MockTransport(handler))
    out = await client.speak("hola", model="canopylabs/orpheus-v1-english", voice="troy")
    assert out == b"WAVDATA"


async def test_on_inbound_returns_transcript(monkeypatch):
    mod = _mod()
    class FakeClient:
        def __init__(self, *a, **k): pass
        async def transcribe(self, audio, **k): return "hola mundo"
    monkeypatch.setattr(mod, "GroqClient", FakeClient)
    skill = mod.build_skill({"stt_model": "whisper-large-v3-turbo"})
    raw = RawInbound(user_id="1", chat_id="2",
                     attachment=Attachment(kind="voice", mime="audio/ogg", data=b"x"))
    assert await skill.on_inbound(raw, _Ctx()) == "hola mundo"


async def test_on_inbound_rejects_oversize(monkeypatch):
    mod = _mod()
    class Boom:
        def __init__(self, *a, **k): pass
        async def transcribe(self, *a, **k): raise AssertionError("must not call Groq")
    monkeypatch.setattr(mod, "GroqClient", Boom)
    skill = mod.build_skill({})
    big = Attachment(kind="voice", mime="audio/ogg", data=b"0" * (25 * 1024 * 1024 + 1))
    raw = RawInbound(user_id="1", chat_id="2", attachment=big)
    assert await skill.on_inbound(raw, _Ctx()) is None


async def test_on_inbound_stt_failure_returns_none(monkeypatch):
    mod = _mod()
    class FailClient:
        def __init__(self, *a, **k): pass
        async def transcribe(self, *a, **k): raise httpx.ConnectError("down")
    monkeypatch.setattr(mod, "GroqClient", FailClient)
    skill = mod.build_skill({})
    raw = RawInbound(user_id="1", chat_id="2",
                     attachment=Attachment(kind="voice", mime="audio/ogg", data=b"x"))
    assert await skill.on_inbound(raw, _Ctx()) is None


async def test_on_outbound_voice_only_when_from_voice(monkeypatch):
    mod = _mod()
    class FakeClient:
        def __init__(self, *a, **k): pass
        async def speak(self, text, **k): return b"WAV"
    monkeypatch.setattr(mod, "GroqClient", FakeClient)
    skill = mod.build_skill({"reply_with_voice": True})
    out = OutgoingMessage(chat_id="2", text="hola")
    voiced = await skill.on_outbound(out, InboundContext(True, "2", "1"), _Ctx())
    assert voiced and voiced[0].kind == "voice" and voiced[0].data == b"WAV"
    assert await skill.on_outbound(out, InboundContext(False, "2", "1"), _Ctx()) is None


async def test_on_outbound_tts_failure_degrades_to_text(monkeypatch):
    mod = _mod()
    class FailClient:
        def __init__(self, *a, **k): pass
        async def speak(self, *a, **k): raise httpx.ConnectError("down")
    monkeypatch.setattr(mod, "GroqClient", FailClient)
    skill = mod.build_skill({"reply_with_voice": True})
    out = OutgoingMessage(chat_id="2", text="hola")
    assert await skill.on_outbound(out, InboundContext(True, "2", "1"), _Ctx()) is None


# ---- diarization (pyannote + HUGGINGFACE_TOKEN, optional) --------------------

def test_label_transcript_two_speakers_labels_each_turn():
    mod = _mod()
    segments = [{"start": 0.0, "end": 2.0, "text": "hola"},
                {"start": 2.0, "end": 4.0, "text": "qué tal"},
                {"start": 4.0, "end": 6.0, "text": "bien vos"}]
    turns = [(0.0, 2.0, "SPEAKER_00"), (2.0, 4.0, "SPEAKER_01"), (4.0, 6.0, "SPEAKER_00")]
    out = mod._label_transcript(segments, turns)
    assert "Hablante 1: hola" in out
    assert "Hablante 2: qué tal" in out
    assert "Hablante 1: bien vos" in out


def test_label_transcript_single_speaker_returns_none():
    mod = _mod()
    segments = [{"start": 0.0, "end": 2.0, "text": "hola"}]
    turns = [(0.0, 2.0, "SPEAKER_00")]
    assert mod._label_transcript(segments, turns) is None


def test_label_transcript_without_turns_returns_none():
    mod = _mod()
    assert mod._label_transcript([{"start": 0, "end": 1, "text": "x"}], []) is None


async def test_on_inbound_diarizes_when_token_and_diarizer_present(monkeypatch):
    mod = _mod()

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def transcribe_segments(self, audio, **k):
            return ("hola qué tal", [{"start": 0.0, "end": 2.0, "text": "hola"},
                                     {"start": 2.0, "end": 4.0, "text": "qué tal"}])
        async def transcribe(self, *a, **k):
            raise AssertionError("should diarize, not plain-transcribe")
    monkeypatch.setattr(mod, "GroqClient", FakeClient)

    async def fake_diar(audio, mime):
        return [(0.0, 2.0, "SPEAKER_00"), (2.0, 4.0, "SPEAKER_01")]

    skill = mod.GroqAudioSkill({}, diarizer_factory=lambda token: fake_diar)
    raw = RawInbound(user_id="1", chat_id="2",
                     attachment=Attachment(kind="audio", mime="audio/ogg", data=b"x"))
    out = await skill.on_inbound(raw, _Ctx(hf="tok"))
    assert "Hablante 1: hola" in out and "Hablante 2: qué tal" in out


async def test_on_inbound_falls_back_to_plain_when_diarizer_unavailable(monkeypatch):
    mod = _mod()

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def transcribe(self, audio, **k): return "hola mundo"
    monkeypatch.setattr(mod, "GroqClient", FakeClient)

    skill = mod.GroqAudioSkill({}, diarizer_factory=lambda token: None)  # pyannote absent
    raw = RawInbound(user_id="1", chat_id="2",
                     attachment=Attachment(kind="audio", mime="audio/ogg", data=b"x"))
    assert await skill.on_inbound(raw, _Ctx(hf="tok")) == "hola mundo"


async def test_on_inbound_falls_back_when_diarization_raises(monkeypatch):
    mod = _mod()

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def transcribe_segments(self, *a, **k): raise RuntimeError("boom")
        async def transcribe(self, audio, **k): return "hola mundo"
    monkeypatch.setattr(mod, "GroqClient", FakeClient)

    async def fake_diar(audio, mime): return [(0, 1, "A"), (1, 2, "B")]

    skill = mod.GroqAudioSkill({}, diarizer_factory=lambda token: fake_diar)
    raw = RawInbound(user_id="1", chat_id="2",
                     attachment=Attachment(kind="audio", mime="audio/ogg", data=b"x"))
    assert await skill.on_inbound(raw, _Ctx(hf="tok")) == "hola mundo"


async def test_on_inbound_no_token_skips_diarization(monkeypatch):
    mod = _mod()

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def transcribe(self, audio, **k): return "hola mundo"
        async def transcribe_segments(self, *a, **k):
            raise AssertionError("no token -> must not diarize")
    monkeypatch.setattr(mod, "GroqClient", FakeClient)

    def boom(token):
        raise AssertionError("no token -> must not build diarizer")

    skill = mod.GroqAudioSkill({}, diarizer_factory=boom)
    raw = RawInbound(user_id="1", chat_id="2",
                     attachment=Attachment(kind="audio", mime="audio/ogg", data=b"x"))
    assert await skill.on_inbound(raw, _Ctx(hf=None)) == "hola mundo"


# ---- speaker identification & enrollment (pyannote embedding) ----------------

def _fake_embedding(val=0.9):
    """Return a unit-norm 512-dim embedding biased toward val."""
    arr = np.full(512, val)
    return arr / np.linalg.norm(arr)


def test_cosine_similarity_identical_vectors():
    mod = _mod()
    v = _fake_embedding(1.0)
    assert abs(mod._cosine_similarity(v, v) - 1.0) < 1e-6


def test_cosine_similarity_orthogonal_vectors():
    mod = _mod()
    a = np.zeros(4); a[0] = 1.0
    b = np.zeros(4); b[1] = 1.0
    assert abs(mod._cosine_similarity(a, b)) < 1e-6


def test_identify_speaker_above_threshold():
    mod = _mod()
    emb = _fake_embedding(0.9)
    profiles = {"Gabriel": emb.copy(), "María": _fake_embedding(-0.9)}
    assert mod._identify_speaker(emb, profiles, threshold=0.75) == "Gabriel"


def test_identify_speaker_below_threshold_returns_none():
    mod = _mod()
    emb = _fake_embedding(0.9)
    profiles = {"Gabriel": _fake_embedding(-0.9)}
    assert mod._identify_speaker(emb, profiles, threshold=0.75) is None


def test_load_voice_profiles_empty_dir(tmp_path):
    mod = _mod()
    assert mod._load_voice_profiles(str(tmp_path)) == {}


def test_load_voice_profiles_reads_npy_files(tmp_path):
    mod = _mod()
    emb = _fake_embedding(0.5)
    np.save(tmp_path / "Gabriel.npy", emb)
    profiles = mod._load_voice_profiles(str(tmp_path))
    assert "Gabriel" in profiles
    assert profiles["Gabriel"].shape == emb.shape


def test_load_voice_profiles_ignores_underscore_files(tmp_path):
    mod = _mod()
    np.save(tmp_path / "_pending.npy", _fake_embedding())
    (tmp_path / "_pending.json").write_text("{}")
    profiles = mod._load_voice_profiles(str(tmp_path))
    assert profiles == {}


async def test_on_inbound_enrolls_pending_and_returns_confirmation(monkeypatch, tmp_path):
    mod = _mod()

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def transcribe(self, *a, **k): return "hola soy Gabriel"

    monkeypatch.setattr(mod, "GroqClient", FakeClient)

    emb = _fake_embedding()

    async def fake_embed(audio, mime):
        return emb

    pending = {"name": "Gabriel", "chat_id": "2", "ts": time.time()}
    (tmp_path / "_pending.json").write_text(json.dumps(pending))

    cfg = {"profiles_dir": str(tmp_path)}
    skill = mod.GroqAudioSkill(cfg,
                                diarizer_factory=lambda t: None,
                                embedder_factory=lambda t: fake_embed)
    raw = RawInbound(user_id="1", chat_id="2",
                     attachment=Attachment(kind="voice", mime="audio/ogg", data=b"x"))
    result = await skill.on_inbound(raw, _Ctx(hf="tok"))
    assert "Gabriel" in result
    assert "✓" in result
    assert (tmp_path / "Gabriel.npy").exists()
    assert not (tmp_path / "_pending.json").exists()


async def test_on_inbound_ignores_expired_pending(monkeypatch, tmp_path):
    mod = _mod()

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def transcribe(self, *a, **k): return "texto"

    monkeypatch.setattr(mod, "GroqClient", FakeClient)

    expired = {"name": "Gabriel", "chat_id": "2", "ts": time.time() - 400}
    (tmp_path / "_pending.json").write_text(json.dumps(expired))

    cfg = {"profiles_dir": str(tmp_path)}
    skill = mod.GroqAudioSkill(cfg,
                                diarizer_factory=lambda t: None,
                                embedder_factory=lambda t: None)
    raw = RawInbound(user_id="1", chat_id="2",
                     attachment=Attachment(kind="voice", mime="audio/ogg", data=b"x"))
    result = await skill.on_inbound(raw, _Ctx(hf="tok"))
    assert result == "texto"
    assert not (tmp_path / "Gabriel.npy").exists()


async def test_on_inbound_identified_speaker_prepends_name(monkeypatch, tmp_path):
    mod = _mod()

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def transcribe(self, *a, **k): return "buenos días"

    monkeypatch.setattr(mod, "GroqClient", FakeClient)

    emb = _fake_embedding(0.9)
    np.save(tmp_path / "Gabriel.npy", emb)

    async def fake_embed(audio, mime):
        return emb.copy()

    cfg = {"profiles_dir": str(tmp_path), "speaker_id_threshold": 0.75}
    skill = mod.GroqAudioSkill(cfg,
                                diarizer_factory=lambda t: None,
                                embedder_factory=lambda t: fake_embed)
    raw = RawInbound(user_id="1", chat_id="2",
                     attachment=Attachment(kind="voice", mime="audio/ogg", data=b"x"))
    result = await skill.on_inbound(raw, _Ctx(hf="tok"))
    assert result == "**Gabriel:** buenos días"


async def test_on_inbound_no_match_returns_plain_transcript(monkeypatch, tmp_path):
    mod = _mod()

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def transcribe(self, *a, **k): return "buenos días"

    monkeypatch.setattr(mod, "GroqClient", FakeClient)

    np.save(tmp_path / "Gabriel.npy", _fake_embedding(0.9))

    async def fake_embed(audio, mime):
        return _fake_embedding(-0.9)  # opposite direction — no match

    cfg = {"profiles_dir": str(tmp_path), "speaker_id_threshold": 0.75}
    skill = mod.GroqAudioSkill(cfg,
                                diarizer_factory=lambda t: None,
                                embedder_factory=lambda t: fake_embed)
    raw = RawInbound(user_id="1", chat_id="2",
                     attachment=Attachment(kind="voice", mime="audio/ogg", data=b"x"))
    result = await skill.on_inbound(raw, _Ctx(hf="tok"))
    assert result == "buenos días"


async def test_on_inbound_embedder_unavailable_falls_through_to_plain(monkeypatch, tmp_path):
    mod = _mod()

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def transcribe(self, *a, **k): return "fallback"

    monkeypatch.setattr(mod, "GroqClient", FakeClient)

    np.save(tmp_path / "Gabriel.npy", _fake_embedding())

    cfg = {"profiles_dir": str(tmp_path)}
    skill = mod.GroqAudioSkill(cfg,
                                diarizer_factory=lambda t: None,
                                embedder_factory=lambda t: None)  # pyannote absent
    raw = RawInbound(user_id="1", chat_id="2",
                     attachment=Attachment(kind="voice", mime="audio/ogg", data=b"x"))
    result = await skill.on_inbound(raw, _Ctx(hf="tok"))
    assert result == "fallback"


async def test_embedder_is_cached_across_calls(monkeypatch, tmp_path):
    """_get_embedder is called once per token, not once per inbound."""
    mod = _mod()

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def transcribe(self, *a, **k): return "texto"

    monkeypatch.setattr(mod, "GroqClient", FakeClient)

    build_count = {"n": 0}

    async def fake_embed(audio, mime):
        return _fake_embedding()

    def counting_factory(token):
        build_count["n"] += 1
        return fake_embed

    np.save(tmp_path / "Gabriel.npy", _fake_embedding())
    cfg = {"profiles_dir": str(tmp_path)}
    skill = mod.GroqAudioSkill(cfg,
                                diarizer_factory=lambda t: None,
                                embedder_factory=counting_factory)
    raw = RawInbound(user_id="1", chat_id="2",
                     attachment=Attachment(kind="voice", mime="audio/ogg", data=b"x"))
    ctx = _Ctx(hf="tok")
    await skill.on_inbound(raw, ctx)
    await skill.on_inbound(raw, ctx)
    assert build_count["n"] == 1  # factory called once; embedder reused
