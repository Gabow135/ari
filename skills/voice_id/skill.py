import json
import os
import time

_PROFILES_DIR = "data/voice_profiles"
_PENDING_FILE = "_pending.json"
_ENROLL_TIMEOUT = 300  # seconds before a pending enrollment expires


class VoiceIdSkill:
    def __init__(self, config):
        self._profiles_dir = config.get("profiles_dir", _PROFILES_DIR)
        self._timeout = config.get("enroll_timeout", _ENROLL_TIMEOUT)

    async def on_inbound(self, raw, ctx):
        text = (raw.text or "").strip()
        if not text.lower().startswith("/enroll"):
            return None

        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            return "Uso: /enroll Nombre (o @Nombre)"
        name = parts[1].lstrip("@").strip()
        if not name:
            return "Uso: /enroll Nombre (o @Nombre)"

        os.makedirs(self._profiles_dir, exist_ok=True)
        pending = {"name": name, "chat_id": raw.chat_id, "ts": time.time()}
        path = os.path.join(self._profiles_dir, _PENDING_FILE)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(pending, f)

        return f"Listo. Envía una nota de voz para enrollar a {name}."


def build_skill(config):
    return VoiceIdSkill(config)
