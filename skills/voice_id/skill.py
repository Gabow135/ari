import json
import os
import time

_PROFILES_DIR = "data/voice_profiles"
_PENDING_FILE = "_pending.json"
_ENROLL_TIMEOUT = 300

_UNSET = object()


def _build_pyannote_embedder(token):
    """Load pyannote/embedding for voice profile extraction.

    Returns an async callable ``(audio_bytes, mime) -> ndarray``, or None when
    pyannote is not installed or the model fails to load (e.g. token not accepted).
    """
    try:
        from pyannote.audio import Inference, Model
    except Exception:
        return None
    try:
        # pyannote.audio 4.x: Inference wraps a loaded Model, and the HuggingFace
        # token is passed to Model.from_pretrained (the 3.x `use_auth_token` kwarg
        # was removed).
        model = Model.from_pretrained("pyannote/embedding", token=token)
        inference = Inference(model, window="whole")
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
                return np.array(inference(path)).flatten()
            finally:
                os.remove(path)

        return await asyncio.to_thread(_run)

    return embed


def _read_pending(profiles_dir, timeout):
    path = os.path.join(profiles_dir, _PENDING_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if time.time() - data.get("ts", 0) > timeout:
            os.remove(path)
            return None
        return data
    except (OSError, json.JSONDecodeError, KeyError):
        return None


def _clear_pending(profiles_dir):
    try:
        os.remove(os.path.join(profiles_dir, _PENDING_FILE))
    except OSError:
        pass


def _save_profile(profiles_dir, name, embedding):
    import numpy as np

    os.makedirs(profiles_dir, exist_ok=True)
    np.save(os.path.join(profiles_dir, f"{name}.npy"), embedding)


def _load_profiles(profiles_dir):
    import numpy as np

    profiles: dict = {}
    try:
        for fname in os.listdir(profiles_dir):
            if fname.startswith("_") or not fname.endswith(".npy"):
                continue
            profiles[fname[:-4]] = np.load(os.path.join(profiles_dir, fname))
    except OSError:
        pass
    return profiles


def _cosine_sim(a, b):
    import numpy as np

    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _identify(embedding, profiles, threshold):
    best_name, best_sim = None, -1.0
    for name, emb in profiles.items():
        sim = _cosine_sim(embedding, emb)
        if sim > best_sim:
            best_name, best_sim = name, sim
    return best_name if best_sim >= threshold else None


class VoiceIdSkill:
    def __init__(self, config, embedder_factory=None):
        self._cfg = config
        self._embedder_factory = embedder_factory or _build_pyannote_embedder
        self._embedder = _UNSET
        self._embedder_token: str | None = None

    def _get_embedder(self, token):
        if self._embedder is _UNSET or self._embedder_token != token:
            self._embedder = self._embedder_factory(token)
            self._embedder_token = token
        return self._embedder

    async def on_inbound(self, raw, ctx):
        text = (raw.text or "").strip()

        if text.lower().startswith("/enroll"):
            return await self._handle_enroll_command(text, raw)

        att = raw.attachment
        if att is None or att.kind not in ("voice", "audio"):
            return None

        token = ctx.optional_secret("HUGGINGFACE_TOKEN")
        if not token:
            return None

        profiles_dir = self._cfg.get("profiles_dir", _PROFILES_DIR)
        timeout = self._cfg.get("enroll_timeout", _ENROLL_TIMEOUT)
        threshold = self._cfg.get("speaker_id_threshold", 0.7)

        pending = _read_pending(profiles_dir, timeout)
        if pending:
            return await self._complete_enrollment(att, token, pending, profiles_dir, ctx)

        profiles = _load_profiles(profiles_dir)
        if not profiles:
            return None

        return await self._identify_speaker(att, token, profiles, threshold, ctx)

    async def _handle_enroll_command(self, text, raw):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            return "Uso: /enroll Nombre (o @Nombre)"
        name = parts[1].lstrip("@").strip()
        if not name:
            return "Uso: /enroll Nombre (o @Nombre)"

        profiles_dir = self._cfg.get("profiles_dir", _PROFILES_DIR)
        os.makedirs(profiles_dir, exist_ok=True)
        pending = {"name": name, "chat_id": raw.chat_id, "ts": time.time()}
        with open(os.path.join(profiles_dir, _PENDING_FILE), "w", encoding="utf-8") as f:
            json.dump(pending, f)

        return f"Listo. Envía una nota de voz para enrollar a {name}."

    async def _complete_enrollment(self, att, token, pending, profiles_dir, ctx):
        try:
            embedder = self._get_embedder(token)
            if embedder is None:
                _clear_pending(profiles_dir)
                ctx.log.warning(
                    "voice_id: enrollment unavailable — pyannote.audio not installed"
                )
                return (
                    "No pude enrollar la voz: falta el componente de reconocimiento "
                    "(pyannote.audio). Instala el extra «diarization» "
                    "(uv sync --all-extras) y reinicia Ari."
                )
            embedding = await embedder(att.data, att.mime)
            _save_profile(profiles_dir, pending["name"], embedding)
            _clear_pending(profiles_dir)
            return f"✓ Perfil de {pending['name']} guardado."
        except Exception as exc:
            ctx.log.warning("voice_id: enrollment failed: %s", exc)
            return None

    async def _identify_speaker(self, att, token, profiles, threshold, ctx):
        try:
            embedder = self._get_embedder(token)
            if embedder is None:
                return None
            embedding = await embedder(att.data, att.mime)
            speaker = _identify(embedding, profiles, threshold)
            if speaker is None:
                return None
            groq_key = ctx.optional_secret("GROQ_API_KEY")
            if groq_key:
                transcript = await self._transcribe(att, groq_key, ctx)
                if transcript:
                    return f"**{speaker}:** {transcript}"
            return f"**{speaker}:** (nota de voz)"
        except Exception as exc:
            ctx.log.warning("voice_id: identification failed: %s", exc)
            return None

    async def _transcribe(self, att, api_key, ctx):
        try:
            import httpx

            stt_model = self._cfg.get("stt_model", "whisper-large-v3-turbo")
            data = {"model": stt_model}
            files = {"file": (att.filename or "voice.ogg", att.data, "application/octet-stream")}
            async with httpx.AsyncClient(timeout=60.0) as c:
                r = await c.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    data=data,
                    files=files,
                )
                r.raise_for_status()
                return r.json()["text"]
        except Exception as exc:
            ctx.log.warning("voice_id: transcription failed: %s", exc)
            return None


def build_skill(config):
    return VoiceIdSkill(config)
