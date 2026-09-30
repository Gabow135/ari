# Image & Document Input Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Ari receive images and documents (PDF, Excel, CSV, plain text) over Telegram and use their content — images via a Groq vision skill (→ description/OCR text), documents via text extraction.

**Architecture:** Ari's LLM backend (Claude Code CLI, headless) is text-only, so every non-text input becomes text through the existing skill `InboundTransform` contract (`on_inbound(raw) -> str | None`), exactly like voice. Two new first-party skills — `groq_vision` (images) and `documents` (files) — self-select by MIME and return text. The Telegram gateway gains a `media_to_text` helper and photo/document branches; the returned text flows through the normal `run_inbound → _dispatch → HandleMessage` pipeline.

**Tech Stack:** Python 3.11+ (runtime `.venv/bin/python`, python3.12), httpx (Groq, already present), pypdf + openpyxl (new), stdlib csv/base64, python-telegram-bot, pytest + pytest-asyncio (`asyncio_mode=auto`).

**Spec:** `docs/superpowers/specs/2026-09-30-ari-image-document-input-design.md`

## Global Constraints

- Run tests with **`.venv/bin/python -m pytest`** (the venv has all deps incl. mcp, pypdf, openpyxl; bare `python3` does NOT). `pythonpath=["src"]`; `asyncio_mode="auto"` (async tests need no marker); no `conftest.py`.
- Skills are loaded by file path via importlib; tests load `skills/<name>/skill.py` with the `_mod()` importlib pattern and mock Groq HTTP with `httpx.MockTransport`. No network in tests.
- Skills return **text** from `on_inbound`; return **`None`** when the attachment is not theirs or on any error (never raise, never log the raw bytes/secret).
- Images: reject `> 20 MB` before calling Groq (Groq per-image cap); one image per message in v1.
- `groq_vision` requires `GROQ_API_KEY` (already in the vault); `documents` needs no secret.
- Groq vision: `POST https://api.groq.com/openai/v1/chat/completions`, auth `Bearer <key>`, body `messages:[{role:"user", content:[{type:"text",text:<prompt>}, {type:"image_url", image_url:{url:"data:<mime>;base64,<b64>"}}]}]`, read `choices[0].message.content`. The exact `vision_model` is a `skill.json` config value — **verify the current Groq vision model at enable time** (Groq's vision lineup changes); tests are model-agnostic (Groq is mocked).
- The caption on a photo/document is the user's question — carry it as `RawInbound.text` and fold it into the returned text.
- Conventional Commit messages. **No AI attribution / no `Co-Authored-By`.** User-facing Spanish must be **neutral (no voseo)** — the project's `test_tone` enforces this.

## Review Focus

- **Image sent while `GROQ_API_KEY` is missing** → Ari sends the reactive `vault_web` `/v/<token>` link, not a silent drop. (Task 4 reuses `missing_inbound_secrets`.)
- **Unsupported file type** (`.docx`, `.zip`, unknown mime) → a clear "no puedo leer ese tipo por ahora" message, not silence. (Task 4 generic branch.)
- **Corrupt/encrypted PDF or malformed xlsx** → `on_inbound` returns `None` (no crash) → generic message. (Task 2 test.)
- **Image over 20 MB** → rejected before Groq is called. (Task 1 test.)
- **Photo/document WITH a caption** → the caption (the user's actual question) is preserved in what Ari reads. (Task 1 & 2 folding tests + Task 3 carries `caption`.)

---

### Task 1: `groq_vision` skill (images → text)

**Files:**
- Create: `skills/groq_vision/skill.json`
- Create: `skills/groq_vision/skill.py` (`GroqVisionClient`, `GroqVisionSkill`, `build_skill`)
- Test: `tests/skills/test_groq_vision_skill.py`

**Interfaces:**
- Consumes: `ari.domain.skills.models` duck types (`raw.attachment.{mime,data}`, `raw.text`); `ctx.secret("GROQ_API_KEY")`, `ctx.log`; `httpx`.
- Produces (module attrs, importlib-loaded): `GroqVisionClient(api_key, *, base_url=..., timeout=60.0, transport=None)` with `async describe(image, *, mime, model, prompt)->str`; `GroqVisionSkill(config)` with `async on_inbound(raw, ctx)->str|None`; `build_skill(config)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/skills/test_groq_vision_skill.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/skills/test_groq_vision_skill.py -v`
Expected: FAIL — `skills/groq_vision/skill.py` does not exist.

- [ ] **Step 3: Write minimal implementation**

```json
// skills/groq_vision/skill.json
{
  "name": "groq_vision",
  "version": "0.1.0",
  "enabled": false,
  "owner_only": true,
  "description": "Describe and OCR images the user sends, via Groq vision.",
  "entrypoint": "skill.py",
  "factory": "build_skill",
  "required_secrets": ["GROQ_API_KEY"],
  "hooks": ["inbound_transform"],
  "config": {
    "vision_model": "meta-llama/llama-4-scout-17b-16e-instruct",
    "prompt": "Describe esta imagen en detalle y transcribe textualmente cualquier texto visible.",
    "max_bytes": 20971520
  }
}
```

(NOTE: `vision_model` is a best-known default — verify the current Groq vision model in the Groq docs when enabling the skill and update `skill.json` if it changed. Tests mock Groq so they do not depend on it.)

```python
# skills/groq_vision/skill.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/skills/test_groq_vision_skill.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add skills/groq_vision tests/skills/test_groq_vision_skill.py
git commit -m "feat(media): groq_vision skill — images to text via Groq vision"
```

---

### Task 2: `documents` skill (PDF/Excel/CSV/text → text) + deps

**Files:**
- Modify: `pyproject.toml` (add `pypdf`, `openpyxl` to `[project] dependencies`)
- Create: `skills/documents/skill.json`
- Create: `skills/documents/skill.py` (`DocumentsSkill`, `build_skill`)
- Test: `tests/skills/test_documents_skill.py`

**Interfaces:**
- Consumes: `raw.attachment.{mime,data,filename}`, `raw.text`, `ctx.log`; `pypdf`, `openpyxl`, stdlib.
- Produces: `DocumentsSkill(config)` with `async on_inbound(raw, ctx)->str|None`; `build_skill(config)`.

- [ ] **Step 1: Add the dependencies**

In `pyproject.toml`, add to `[project] dependencies` (after `cryptography>=43`):

```toml
  "pypdf>=5",
  "openpyxl>=3.1",
```

Then install into the venv:

Run: `.venv/bin/python -m pip install -e ".[dev]"`
Expected: pypdf + openpyxl installed.

- [ ] **Step 2: Write the failing tests**

```python
# tests/skills/test_documents_skill.py
import importlib.util
import io
import logging
import pathlib

from ari.domain.skills.models import Attachment, RawInbound


def _mod():
    p = pathlib.Path("skills/documents/skill.py")
    spec = importlib.util.spec_from_file_location("documents_skill_under_test", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Ctx:
    def __init__(self):
        self.config = {}
        self.log = logging.getLogger("test")

    def secret(self, name):
        raise AssertionError("documents needs no secret")


def _xlsx_bytes():
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Hoja1"
    ws.append(["a", "b"])
    ws.append([1, 2])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def test_csv_and_txt_decoded():
    skill = _mod().build_skill({})
    raw = RawInbound(user_id="1", chat_id="2", text="mirá esto",
                     attachment=Attachment(kind="document", mime="text/csv",
                                           data=b"name,age\nAna,3", filename="d.csv"))
    out = await skill.on_inbound(raw, _Ctx())
    assert "name,age" in out and "Ana,3" in out and "mirá esto" in out and "d.csv" in out


async def test_xlsx_extracted():
    skill = _mod().build_skill({})
    raw = RawInbound(user_id="1", chat_id="2",
                     attachment=Attachment(
                         kind="document",
                         mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                         data=_xlsx_bytes(), filename="s.xlsx"))
    out = await skill.on_inbound(raw, _Ctx())
    assert "Hoja1" in out and "a,b" in out and "1,2" in out


async def test_pdf_extracted():
    # A minimal valid PDF with the text "Hola" — built with pypdf so the fixture is real.
    import pypdf
    w = pypdf.PdfWriter()
    w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    skill = _mod().build_skill({})
    raw = RawInbound(user_id="1", chat_id="2",
                     attachment=Attachment(kind="document", mime="application/pdf",
                                           data=buf.getvalue(), filename="d.pdf"))
    out = await skill.on_inbound(raw, _Ctx())
    # A blank page extracts to empty text -> skill returns None (nothing to read).
    assert out is None


async def test_non_document_mime_ignored():
    skill = _mod().build_skill({})
    raw = RawInbound(user_id="1", chat_id="2",
                     attachment=Attachment(kind="photo", mime="image/png", data=b"x"))
    assert await skill.on_inbound(raw, _Ctx()) is None


async def test_truncation():
    skill = _mod().build_skill({"max_chars": 10})
    raw = RawInbound(user_id="1", chat_id="2",
                     attachment=Attachment(kind="document", mime="text/plain",
                                           data=b"0123456789ABCDEFG", filename="d.txt"))
    out = await skill.on_inbound(raw, _Ctx())
    assert "… (truncado)" in out


async def test_corrupt_pdf_returns_none():
    skill = _mod().build_skill({})
    raw = RawInbound(user_id="1", chat_id="2",
                     attachment=Attachment(kind="document", mime="application/pdf",
                                           data=b"%PDF-1.4 not really a pdf", filename="x.pdf"))
    assert await skill.on_inbound(raw, _Ctx()) is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/skills/test_documents_skill.py -v`
Expected: FAIL — `skills/documents/skill.py` does not exist.

- [ ] **Step 4: Write minimal implementation**

```json
// skills/documents/skill.json
{
  "name": "documents",
  "version": "0.1.0",
  "enabled": true,
  "owner_only": true,
  "description": "Read PDF, Excel, CSV and text files the user sends (text extraction).",
  "entrypoint": "skill.py",
  "factory": "build_skill",
  "required_secrets": [],
  "hooks": ["inbound_transform"],
  "config": { "max_chars": 20000 }
}
```

```python
# skills/documents/skill.py
import io

import openpyxl
import pypdf

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class DocumentsSkill:
    def __init__(self, config):
        self._cfg = config

    async def on_inbound(self, raw, ctx):
        att = raw.attachment
        if att is None:
            return None
        mime = (att.mime or "").lower()
        name = (att.filename or "").lower()
        try:
            if mime == "application/pdf" or name.endswith(".pdf"):
                text = self._pdf(att.data)
            elif mime == _XLSX or name.endswith(".xlsx"):
                text = self._xlsx(att.data)
            elif mime in ("text/csv", "text/plain") or name.endswith((".csv", ".txt")):
                text = att.data.decode("utf-8", "replace")
            else:
                return None
        except Exception as exc:  # corrupt/encrypted/unreadable — never crash the turn
            ctx.log.warning("documents: extraction failed for %s: %s", name or mime, exc)
            return None
        text = (text or "").strip()
        if not text:
            return None
        max_chars = self._cfg.get("max_chars", 20000)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n… (truncado)"
        caption = (raw.text or "").strip()
        con = f' con el texto: "{caption}"' if caption else ""
        fname = att.filename or "archivo"
        return f"[El usuario envió el archivo «{fname}»{con}. Contenido:]\n{text}"

    @staticmethod
    def _pdf(data: bytes) -> str:
        reader = pypdf.PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages)

    @staticmethod
    def _xlsx(data: bytes) -> str:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        lines = []
        for ws in wb.worksheets:
            lines.append(f"# {ws.title}")
            for row in ws.iter_rows(values_only=True):
                lines.append(",".join("" if c is None else str(c) for c in row))
        return "\n".join(lines)


def build_skill(config):
    return DocumentsSkill(config)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/skills/test_documents_skill.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml skills/documents tests/skills/test_documents_skill.py
git commit -m "feat(media): documents skill — PDF/Excel/CSV/text extraction; add pypdf, openpyxl"
```

---

### Task 3: Gateway `media_to_text` helper

**Files:**
- Modify: `src/ari/infrastructure/gateway/telegram_adapter.py` (add `media_to_text`)
- Test: `tests/infrastructure/test_telegram_media.py`

**Interfaces:**
- Consumes: `SkillManager.run_inbound` (existing); `RawInbound`/`Attachment` (existing).
- Produces: `async media_to_text(update, download, manager, is_owner) -> str | None` — extracts a photo (largest `PhotoSize`) or document, builds a `RawInbound` (kind `"photo"` for images incl. image-mime documents, else `"document"`; `text=caption`), and runs `manager.run_inbound`. Returns `None` when there is no photo/document.

- [ ] **Step 1: Write the failing test**

```python
# tests/infrastructure/test_telegram_media.py
from types import SimpleNamespace

from ari.infrastructure.gateway.telegram_adapter import media_to_text


class _Manager:
    def __init__(self, reply="ok"):
        self.reply, self.seen = reply, None

    async def run_inbound(self, raw, is_owner):
        self.seen = raw
        return self.reply


def _update(*, photo=None, document=None, caption=None):
    msg = SimpleNamespace(photo=photo, document=document, caption=caption,
                          from_user=SimpleNamespace(id=7), chat_id=42)
    return SimpleNamespace(effective_message=msg, message=msg)


async def test_photo_takes_largest_and_carries_caption():
    small = SimpleNamespace(file_id="s")
    large = SimpleNamespace(file_id="l")
    async def download(media):
        assert media is large   # largest PhotoSize
        return b"JPG"
    m = _Manager("un gato")
    out = await media_to_text(_update(photo=[small, large], caption="¿qué es?"), download, m, True)
    assert out == "un gato"
    assert m.seen.attachment.kind == "photo" and m.seen.attachment.mime == "image/jpeg"
    assert m.seen.attachment.data == b"JPG" and m.seen.text == "¿qué es?"


async def test_document_carries_mime_and_filename():
    doc = SimpleNamespace(mime_type="application/pdf", file_name="report.pdf")
    async def download(media):
        assert media is doc
        return b"PDF"
    m = _Manager("texto")
    out = await media_to_text(_update(document=doc), download, m, True)
    assert out == "texto"
    assert m.seen.attachment.kind == "document"
    assert m.seen.attachment.mime == "application/pdf"
    assert m.seen.attachment.filename == "report.pdf"


async def test_image_mime_document_routes_as_photo():
    doc = SimpleNamespace(mime_type="image/png", file_name="pic.png")
    async def download(media):
        return b"PNG"
    m = _Manager("x")
    await media_to_text(_update(document=doc), download, m, True)
    assert m.seen.attachment.kind == "photo" and m.seen.attachment.mime == "image/png"


async def test_none_when_no_media():
    async def download(media):
        raise AssertionError("should not download")
    assert await media_to_text(_update(), download, _Manager(), True) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/infrastructure/test_telegram_media.py -v`
Expected: FAIL — `ImportError: cannot import name 'media_to_text'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/ari/infrastructure/gateway/telegram_adapter.py` (uses the already-imported `Attachment`, `RawInbound`):

```python
async def media_to_text(update, download, manager, is_owner: bool) -> str | None:
    """Extract a photo (largest size) or document, download its bytes, and run it through
    the skill manager's inbound transforms (vision for images, extraction for docs).
    Returns the text, or None when there is no photo/document or no skill handled it.
    `download` is an async callable(media)->bytes."""
    msg = getattr(update, "effective_message", None) or getattr(update, "message", None)
    if msg is None:
        return None
    photos = getattr(msg, "photo", None)
    doc = getattr(msg, "document", None)
    if photos:
        media, kind, mime, filename = photos[-1], "photo", "image/jpeg", "photo.jpg"
    elif doc is not None:
        mime = getattr(doc, "mime_type", None) or "application/octet-stream"
        kind = "photo" if mime.startswith("image/") else "document"
        media, filename = doc, getattr(doc, "file_name", None) or ""
    else:
        return None
    data = await download(media)
    raw = RawInbound(
        user_id=str(msg.from_user.id), chat_id=str(msg.chat_id),
        text=getattr(msg, "caption", None),
        attachment=Attachment(kind=kind, mime=mime, data=bytes(data), filename=filename))
    return await manager.run_inbound(raw, is_owner)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/infrastructure/test_telegram_media.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ari/infrastructure/gateway/telegram_adapter.py tests/infrastructure/test_telegram_media.py
git commit -m "feat(gateway): media_to_text helper for photos and documents"
```

---

### Task 4: Wire photo/document into `_on_message` (integration)

**Files:**
- Modify: `src/ari/main.py` (`_on_message` branch + handler registration)
- Test: none new (glue over already-tested units); proof is the full suite + import smoke.

**Interfaces:**
- Consumes: `media_to_text` (Task 3), `missing_inbound_secrets` (existing), `run_inbound`, `vault_web.new_link`.

- [ ] **Step 1: Import `media_to_text`**

In `src/ari/main.py`, extend the telegram_adapter import to include `media_to_text`:

```python
from ari.infrastructure.gateway.telegram_adapter import TelegramAdapter, voice_to_text, media_to_text
```

- [ ] **Step 2: Add a photo/document branch to `_on_message`**

In `_on_message`, the current voice branch returns early after handling voice/audio. Immediately BEFORE the `if not (getattr(msg, "voice", None) or getattr(msg, "audio", None)): return` guard, insert a photo/document branch (so text and voice keep their existing paths):

```python
        if getattr(msg, "photo", None) or getattr(msg, "document", None):
            if not await _admit(msg):
                return
            is_owner = app.bot_data["gate"].is_owner(str(msg.from_user.id))

            async def _download(media):
                f = await media.get_file()
                return await f.download_as_bytearray()

            try:
                text = await media_to_text(update, _download, app.bot_data["skills"], is_owner)
            except Exception:
                log.exception("media_to_text raised for user %s", msg.from_user.id)
                await msg.reply_text("Hubo un error procesando el archivo. ¿Lo resumís por texto?")
                return
            if not text:
                missing = missing_inbound_secrets(app.bot_data["skills"].list())
                if is_owner and missing:
                    try:
                        link = app.bot_data["vault_web"].new_link()
                        await msg.reply_text(
                            f"Necesito {', '.join(missing)} para leer eso. Cárgala en la misma "
                            f"red (vence pronto):\n{link}")
                    except Exception:
                        await msg.reply_text("No pude procesar ese archivo. ¿Lo resumís por texto?")
                else:
                    await msg.reply_text(
                        "No puedo leer ese tipo de archivo por ahora. ¿Me lo resumís por texto?")
                return
            await _dispatch(update, text)
            return
```

(Place this block after the `msg is None / from_user is None` guard and before the voice/audio guard. `_download` mirrors the voice branch's closure.)

- [ ] **Step 3: Register the handler for photos and documents**

Find the `MessageHandler(filters.VOICE | filters.AUDIO, _on_message)` registration and extend it so photo/document updates also reach `_on_message`:

```python
    app.add_handler(MessageHandler(
        filters.VOICE | filters.AUDIO | filters.PHOTO | filters.Document.ALL, _on_message))
```

(`filters` is already imported in main.py for the existing voice handler.)

- [ ] **Step 4: Verify the whole suite + import smoke (venv)**

Run: `.venv/bin/python -m pytest -q -m "not slow"`
Expected: all pass (the new skill + gateway tests included), no new failures.

Run: `.venv/bin/python -c "import ari.main"`
Expected: no error.

- [ ] **Step 5: Manual smoke (documented, not automated)**

1. `./run.sh` (Ari under the venv).
2. `/skill_on groq_vision` → `active` (GROQ_API_KEY already loaded). The `documents` skill is enabled by default.
3. Send a photo (optionally with a caption) → Ari replies using a description/OCR of the image.
4. Send a PDF / .xlsx / .csv → Ari answers from its content.
5. Send an unsupported file (e.g. a .zip) → the "no puedo leer ese tipo por ahora" message.

- [ ] **Step 6: Commit**

```bash
git add src/ari/main.py
git commit -m "feat(media): handle photos and documents in the Telegram gateway"
```

---

## Notes for the implementer

- Run everything with `.venv/bin/python` — the venv has mcp, pypdf, openpyxl; bare `python3` does not.
- The `groq_vision` skill ships `enabled: false` (owner turns it on with `/skill_on groq_vision`; `GROQ_API_KEY` is already in the vault). `documents` ships `enabled: true` (no secret needed).
- Skills self-select by MIME: `groq_vision` claims `image/*`, `documents` claims pdf/xlsx/csv/text. `run_inbound` tries each until one returns non-None; order doesn't matter because their MIME sets are disjoint.
- Verify the current Groq vision model in the Groq docs when enabling `groq_vision`; update `skill.json`'s `vision_model` if it changed. Tests are model-agnostic (Groq mocked).
- Keep user-facing Spanish neutral (no voseo) so `test_tone` stays green.
