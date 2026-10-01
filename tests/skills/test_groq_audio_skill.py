import importlib.util
import logging
import pathlib

import httpx

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
