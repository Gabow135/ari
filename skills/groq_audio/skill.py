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

    async def transcribe_segments(self, audio, *, model, filename="voice.ogg", language=None):
        """Verbose transcription: returns ``(text, segments)`` where each segment
        carries start/end/text — needed to align words with the diarization turns."""
        data = {"model": model, "response_format": "verbose_json"}
        if language:
            data["language"] = language
        files = {"file": (filename, audio, "application/octet-stream")}
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as c:
            r = await c.post(f"{self._base}/audio/transcriptions",
                             headers={"Authorization": f"Bearer {self._key}"},
                             data=data, files=files)
            r.raise_for_status()
            j = r.json()
            return j.get("text", ""), j.get("segments", [])

    async def speak(self, text, *, model, voice, response_format="wav"):
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as c:
            r = await c.post(f"{self._base}/audio/speech",
                             headers={"Authorization": f"Bearer {self._key}"},
                             json={"model": model, "input": text, "voice": voice,
                                   "response_format": response_format})
            r.raise_for_status()
            return r.content


def _speaker_for(seg, turns):
    """The diarization speaker whose turn overlaps this transcript segment most.
    Falls back to the nearest turn when a segment lands between turns."""
    s, e = seg["start"], seg["end"]
    best, best_overlap = None, 0.0
    for start, end, spk in turns:
        overlap = min(e, end) - max(s, start)
        if overlap > best_overlap:
            best, best_overlap = spk, overlap
    if best is None and turns:
        mid = (s + e) / 2
        best = min(turns, key=lambda t: min(abs(mid - t[0]), abs(mid - t[1])))[2]
    return best


def _label_transcript(segments, turns):
    """Turn transcript segments + speaker turns into "Hablante N: …" lines.
    Returns None when there is nothing to label or only one speaker (a plain
    single-voice note needs no labels)."""
    if not segments or not turns:
        return None
    labels: dict = {}
    order: list = []

    def label(spk):
        if spk not in labels:
            order.append(spk)
            labels[spk] = f"Hablante {len(order)}"
        return labels[spk]

    lines: list = []
    current = object()  # sentinel: never equals a real speaker
    buf: list = []
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        spk = _speaker_for(seg, turns)
        if spk != current:
            if buf:
                lines.append(f"{label(current)}: {' '.join(buf)}")
                buf = []
            current = spk
        buf.append(text)
    if buf:
        lines.append(f"{label(current)}: {' '.join(buf)}")
    if len(order) < 2:
        return None
    return "\n".join(lines)


def _build_pyannote_diarizer(token):
    """Default diarizer: lazily load pyannote.audio. Returns an async callable
    ``(audio_bytes, mime) -> [(start, end, speaker)]``, or None when pyannote is
    not installed — so the skill degrades to plain transcription. Also requires
    the user to have accepted the model's conditions on Hugging Face for the
    token to work; if loading fails, returns None and we fall back."""
    try:
        from pyannote.audio import Pipeline
    except Exception:
        return None
    try:
        pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1",
                                            use_auth_token=token)
    except Exception:
        return None

    async def diarize(audio_bytes, mime):
        import asyncio
        import os
        import tempfile

        def _run():
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
                f.write(audio_bytes)
                path = f.name
            try:
                annotation = pipeline(path)
                return [(turn.start, turn.end, spk)
                        for turn, _, spk in annotation.itertracks(yield_label=True)]
            finally:
                os.remove(path)

        return await asyncio.to_thread(_run)

    return diarize


class GroqAudioSkill:
    def __init__(self, config, diarizer_factory=None):
        self._cfg = config
        # Injected in tests; defaults to the real (lazy) pyannote builder.
        self._diarizer_factory = diarizer_factory or _build_pyannote_diarizer

    async def on_inbound(self, raw, ctx):
        att = raw.attachment
        if att is None or att.kind not in ("voice", "audio"):
            return None
        if len(att.data) > _MAX_BYTES:
            ctx.log.warning("groq_audio: audio too large (%d bytes)", len(att.data))
            return None
        try:
            client = GroqClient(ctx.secret("GROQ_API_KEY"))
            token = ctx.optional_secret("HUGGINGFACE_TOKEN")
            if token:
                labeled = await self._diarized(client, att, token, ctx)
                if labeled:
                    return labeled
            text = await client.transcribe(
                att.data, model=self._cfg.get("stt_model", "whisper-large-v3-turbo"),
                filename=att.filename or "voice.ogg")
            return text or None
        except Exception as exc:  # network/HTTP/parse — fall back to a text reply
            ctx.log.warning("groq_audio: transcription failed: %s", exc)
            return None

    async def _diarized(self, client, att, token, ctx):
        """Transcribe with timestamps, diarize, and align into "Hablante N: …".
        Any failure (pyannote absent, conditions not accepted, single speaker)
        returns None so on_inbound falls back to a plain transcript."""
        try:
            diarizer = self._diarizer_factory(token)
            if diarizer is None:
                return None
            _text, segments = await client.transcribe_segments(
                att.data, model=self._cfg.get("stt_model", "whisper-large-v3-turbo"),
                filename=att.filename or "voice.ogg")
            turns = await diarizer(att.data, att.mime)
            return _label_transcript(segments, turns)
        except Exception as exc:
            ctx.log.warning("groq_audio: diarization failed, using plain transcript: %s", exc)
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
