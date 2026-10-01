import json
import os
import time

import httpx

from ari.domain.skills.models import Delivery

_BASE = "https://api.groq.com/openai/v1"
_MAX_BYTES = 25 * 1024 * 1024  # Groq free-tier transcription limit
_PROFILES_DIR = "data/voice_profiles"
_PENDING_FILE = "_pending.json"
_ENROLL_TIMEOUT = 300  # seconds

_UNSET = object()  # sentinel for uninitialized cached instances


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


def _build_pyannote_embedder(token):
    """Load pyannote embedding model for speaker identification.
    Returns an async callable ``(audio_bytes, mime) -> numpy.ndarray | None``,
    or None when pyannote is not installed or the model fails to load.
    Requires the user to have accepted pyannote/embedding conditions on HF."""
    try:
        from pyannote.audio import Inference
    except Exception:
        return None
    try:
        model = Inference("pyannote/embedding", window="whole", use_auth_token=token)
    except Exception:
        return None

    async def embed(audio_bytes, mime):
        import asyncio
        import tempfile
        import numpy as np

        suffix = ".ogg" if "ogg" in (mime or "") else ".wav"

        def _run():
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                f.write(audio_bytes)
                path = f.name
            try:
                result = model(path)
                return np.array(result).flatten()
            finally:
                os.remove(path)

        return await asyncio.to_thread(_run)

    return embed


# ---- voice profile helpers ---------------------------------------------------

def _read_pending(profiles_dir):
    """Read pending enrollment. Returns dict or None if absent/expired."""
    path = os.path.join(profiles_dir, _PENDING_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if time.time() - data.get("ts", 0) > _ENROLL_TIMEOUT:
            os.remove(path)
            return None
        return data
    except (OSError, json.JSONDecodeError, KeyError):
        return None


def _clear_pending(profiles_dir):
    path = os.path.join(profiles_dir, _PENDING_FILE)
    try:
        os.remove(path)
    except OSError:
        pass


def _save_profile(profiles_dir, name, embedding):
    import numpy as np
    os.makedirs(profiles_dir, exist_ok=True)
    np.save(os.path.join(profiles_dir, f"{name}.npy"), embedding)


def _load_voice_profiles(profiles_dir):
    """Load all saved voice profile embeddings. Returns {name: ndarray}."""
    import numpy as np
    profiles: dict = {}
    try:
        for fname in os.listdir(profiles_dir):
            if fname.startswith("_") or not fname.endswith(".npy"):
                continue
            name = fname[:-4]
            profiles[name] = np.load(os.path.join(profiles_dir, fname))
    except OSError:
        pass
    return profiles


def _cosine_similarity(a, b):
    import numpy as np
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _identify_speaker(embedding, profiles, threshold=0.75):
    """Return the best-matching profile name, or None if below threshold."""
    best_name, best_sim = None, -1.0
    for name, profile_emb in profiles.items():
        sim = _cosine_similarity(embedding, profile_emb)
        if sim > best_sim:
            best_name, best_sim = name, sim
    return best_name if best_sim >= threshold else None


# ---- skill -------------------------------------------------------------------

class GroqAudioSkill:
    def __init__(self, config, diarizer_factory=None, embedder_factory=None):
        self._cfg = config
        self._diarizer_factory = diarizer_factory or _build_pyannote_diarizer
        self._embedder_factory = embedder_factory or _build_pyannote_embedder
        # Cached per HF token so the heavy model is only loaded once.
        self._embedder = _UNSET
        self._embedder_token: str | None = None

    def _get_embedder(self, token):
        if self._embedder is _UNSET or self._embedder_token != token:
            self._embedder = self._embedder_factory(token)
            self._embedder_token = token
        return self._embedder

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
            profiles_dir = self._cfg.get("profiles_dir", _PROFILES_DIR)

            if token:
                # 1. Enrollment takes priority: consume the pending sample.
                pending = _read_pending(profiles_dir)
                if pending:
                    enrolled = await self._enroll(client, att, token, pending, profiles_dir, ctx)
                    if enrolled:
                        return enrolled

                # 2. Named-speaker identification (beats generic diarization).
                profiles = _load_voice_profiles(profiles_dir)
                if profiles:
                    identified = await self._identified(client, att, token, profiles, ctx)
                    if identified:
                        return identified

                # 3. Generic diarization (unnamed speakers).
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

    async def _enroll(self, client, att, token, pending, profiles_dir, ctx):
        """Extract speaker embedding from the sample and persist the profile."""
        try:
            embedder = self._get_embedder(token)
            if embedder is None:
                return None
            embedding = await embedder(att.data, att.mime)
            _save_profile(profiles_dir, pending["name"], embedding)
            _clear_pending(profiles_dir)
            text = await client.transcribe(
                att.data, model=self._cfg.get("stt_model", "whisper-large-v3-turbo"),
                filename=att.filename or "voice.ogg")
            name = pending["name"]
            msg = f"✓ Perfil de {name} guardado."
            if text:
                msg += f' Dijiste: "{text}"'
            return msg
        except Exception as exc:
            ctx.log.warning("groq_audio: enrollment failed: %s", exc)
            return None

    async def _identified(self, client, att, token, profiles, ctx):
        """Identify speaker from saved profiles and return a labeled transcription."""
        try:
            embedder = self._get_embedder(token)
            if embedder is None:
                return None
            embedding = await embedder(att.data, att.mime)
            threshold = self._cfg.get("speaker_id_threshold", 0.75)
            speaker = _identify_speaker(embedding, profiles, threshold)
            text = await client.transcribe(
                att.data, model=self._cfg.get("stt_model", "whisper-large-v3-turbo"),
                filename=att.filename or "voice.ogg")
            if not text:
                return None
            if speaker:
                return f"**{speaker}:** {text}"
            return text
        except Exception as exc:
            ctx.log.warning("groq_audio: speaker identification failed: %s", exc)
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
