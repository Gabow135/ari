import base64

import httpx

_BASE = "https://api.groq.com/openai/v1"
_MAX_BYTES = 20 * 1024 * 1024  # Groq vision per-image cap


class GroqVisionClient:
    def __init__(self, api_key, *, base_url=_BASE, timeout=60.0, transport=None):
        self._key, self._base, self._timeout, self._transport = api_key, base_url, timeout, transport

    async def describe(self, image, *, mime, model, prompt):
        b64 = base64.b64encode(image).decode("ascii")
        body = {"model": model, "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]}]}
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as c:
            r = await c.post(f"{self._base}/chat/completions",
                             headers={"Authorization": f"Bearer {self._key}"}, json=body)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]


class GroqVisionSkill:
    def __init__(self, config):
        self._cfg = config

    async def on_inbound(self, raw, ctx):
        att = raw.attachment
        if att is None or not (att.mime or "").startswith("image/"):
            return None
        if len(att.data) > self._cfg.get("max_bytes", _MAX_BYTES):
            ctx.log.warning("groq_vision: image too large (%d bytes)", len(att.data))
            return None
        try:
            client = GroqVisionClient(ctx.secret("GROQ_API_KEY"))
            desc = await client.describe(
                att.data, mime=att.mime,
                model=self._cfg.get("vision_model", "meta-llama/llama-4-scout-17b-16e-instruct"),
                prompt=self._cfg.get("prompt", "Describe esta imagen en detalle y transcribe el texto visible."))
        except Exception as exc:  # network/HTTP/parse — never crash the turn
            ctx.log.warning("groq_vision: describe failed: %s", exc)
            return None
        desc = (desc or "").strip()
        if not desc:
            return None
        caption = (raw.text or "").strip()
        con = f' con el texto: "{caption}"' if caption else ""
        return f"[El usuario envió una imagen{con}. Esto es lo que muestra:]\n{desc}"


def build_skill(config):
    return GroqVisionSkill(config)
