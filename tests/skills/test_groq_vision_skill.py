import importlib.util
import logging
import pathlib

import httpx

from ari.domain.skills.models import Attachment, RawInbound


def _mod():
    p = pathlib.Path("skills/groq_vision/skill.py")
    spec = importlib.util.spec_from_file_location("groq_vision_skill_under_test", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Ctx:
    def __init__(self, key="k"):
        self._key = key
        self.config = {}
        self.log = logging.getLogger("test")

    def secret(self, name):
        return self._key


async def test_describe_posts_image_url_block():
    seen = {}
    def handler(request):
        assert request.url.path == "/openai/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer k"
        import json
        body = json.loads(request.content)
        seen["body"] = body
        return httpx.Response(200, json={"choices": [{"message": {"content": "un gato"}}]})
    client = _mod().GroqVisionClient("k", transport=httpx.MockTransport(handler))
    out = await client.describe(b"\x89PNG", mime="image/png", model="m", prompt="describe")
    assert out == "un gato"
    content = seen["body"]["messages"][0]["content"]
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_on_inbound_returns_description_with_caption(monkeypatch):
    mod = _mod()
    class FakeClient:
        def __init__(self, *a, **k): pass
        async def describe(self, image, **k): return "una factura por $100"
    monkeypatch.setattr(mod, "GroqVisionClient", FakeClient)
    skill = mod.build_skill({"vision_model": "m"})
    raw = RawInbound(user_id="1", chat_id="2", text="¿cuánto es el total?",
                     attachment=Attachment(kind="photo", mime="image/jpeg", data=b"x"))
    out = await skill.on_inbound(raw, _Ctx())
    assert "una factura por $100" in out
    assert "¿cuánto es el total?" in out


async def test_on_inbound_ignores_non_image(monkeypatch):
    mod = _mod()
    class Boom:
        def __init__(self, *a, **k): pass
        async def describe(self, *a, **k): raise AssertionError("must not call Groq")
    monkeypatch.setattr(mod, "GroqVisionClient", Boom)
    skill = mod.build_skill({})
    raw = RawInbound(user_id="1", chat_id="2",
                     attachment=Attachment(kind="document", mime="application/pdf", data=b"x"))
    assert await skill.on_inbound(raw, _Ctx()) is None


async def test_on_inbound_rejects_oversize(monkeypatch):
    mod = _mod()
    class Boom:
        def __init__(self, *a, **k): pass
        async def describe(self, *a, **k): raise AssertionError("must not call Groq")
    monkeypatch.setattr(mod, "GroqVisionClient", Boom)
    skill = mod.build_skill({})
    big = Attachment(kind="photo", mime="image/jpeg", data=b"0" * (20 * 1024 * 1024 + 1))
    raw = RawInbound(user_id="1", chat_id="2", attachment=big)
    assert await skill.on_inbound(raw, _Ctx()) is None


async def test_on_inbound_http_failure_returns_none(monkeypatch):
    mod = _mod()
    class FailClient:
        def __init__(self, *a, **k): pass
        async def describe(self, *a, **k): raise httpx.ConnectError("down")
    monkeypatch.setattr(mod, "GroqVisionClient", FailClient)
    skill = mod.build_skill({})
    raw = RawInbound(user_id="1", chat_id="2",
                     attachment=Attachment(kind="photo", mime="image/png", data=b"x"))
    assert await skill.on_inbound(raw, _Ctx()) is None
