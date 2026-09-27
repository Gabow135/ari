import httpx

from ari.domain.skills.models import Delivery

_BASE = "https://api.groq.com/openai/v1"
_MAX_BYTES = 25 * 1024 * 1024  # Groq free-tier transcription limit


class GroqClient:
    def __init__(self, api_key, *, base_url=_BASE, timeout=60.0, transport=None):
        self._key, self._base, self._timeout, self._transport = api_key, base_url, timeout, transport

    async def transcribe(self, audio, *, model, filename="voice.ogg", language=None):
        data = {"model": model}
        if language:
            data["language"] = language
        files = {"file": (filename, audio, "application/octet-stream")}
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as c:
            r = await c.post(f"{self._base}/audio/transcriptions",
                             headers={"Authorization": f"Bearer {self._key}"},
                             data=data, files=files)
            r.raise_for_status()
            return r.json()["text"]

    async def speak(self, text, *, model, voice, response_format="wav"):
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as c:
            r = await c.post(f"{self._base}/audio/speech",
                             headers={"Authorization": f"Bearer {self._key}"},
                             json={"model": model, "input": text, "voice": voice,
                                   "response_format": response_format})
            r.raise_for_status()
            return r.content


class GroqAudioSkill:
    def __init__(self, config):
        self._cfg = config

    async def on_inbound(self, raw, ctx):
        att = raw.attachment
        if att is None or att.kind not in ("voice", "audio"):
            return None
        if len(att.data) > _MAX_BYTES:
            ctx.log.warning("groq_audio: audio too large (%d bytes)", len(att.data))
            return None
        try:
            client = GroqClient(ctx.secret("GROQ_API_KEY"))
            text = await client.transcribe(
                att.data, model=self._cfg.get("stt_model", "whisper-large-v3-turbo"),
                filename=att.filename or "voice.ogg")
            return text or None
        except Exception as exc:  # network/HTTP/parse — fall back to a text reply
            ctx.log.warning("groq_audio: transcription failed: %s", exc)
            return None

    async def on_outbound(self, reply, origin, ctx):
        if not origin.came_from_voice or not self._cfg.get("reply_with_voice", True):
            return None
        try:
            client = GroqClient(ctx.secret("GROQ_API_KEY"))
            audio = await client.speak(
                reply.text, model=self._cfg.get("tts_model", "canopylabs/orpheus-v1-english"),
                voice=self._cfg.get("tts_voice", "troy"))
            return [Delivery(kind="voice", data=audio, mime="audio/wav")]
        except Exception as exc:  # degrade to text only
            ctx.log.warning("groq_audio: tts failed: %s", exc)
            return None


def build_skill(config):
    return GroqAudioSkill(config)
