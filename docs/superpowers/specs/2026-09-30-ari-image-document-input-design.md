# Ari — Image & document input (Design)

- **Date:** 2026-09-30
- **Status:** Draft — pending user approval
- **Part of:** Multimodal input for Ari (voice already done via `groq_audio`; this adds **images and documents**).
- **Builds on:** the skill system (`domain/skills` models + `InboundTransform` contract, `SkillManager.run_inbound`), the Groq integration (`GROQ_API_KEY` already in the vault, `groq_audio` skill as the pattern), the Telegram gateway voice path (`_on_message` + `voice_to_text`), and the reactive credential path (`missing_inbound_secrets` → `vault_web` link).

## 1. Purpose

Let the owner send Ari an **image** or a **document** (PDF, Excel, CSV, plain text) over
Telegram and have Ari understand and use its content in the conversation.

Ari's LLM backend is the **Claude Code CLI in headless mode, which is text-only** (the
conversation is fed to `claude -p` as plain text on stdin). So every non-text input must
become **text before the LLM**. This is exactly what the skill `InboundTransform` contract
already does (`on_inbound(raw) -> str | None`), so images and documents reuse it — just like
voice does.

- **Images** → a `groq_vision` skill sends the image to a Groq vision model and returns a
  detailed description + OCR of any text, which Ari reads as text.
- **Documents** → a `documents` skill extracts text (PDF via `pypdf`, Excel via `openpyxl`,
  CSV/plain-text decoded) and returns it.

Success: the owner sends a photo of a receipt with the caption "¿cuánto es el total?" → Ari
replies using the transcribed amounts; the owner sends a PDF → Ari answers questions about
its text.

**Why not native Claude vision (verified, out of scope):** a spike confirmed the Claude Code
CLI headless mode accepts **no** image/PDF content (text-only stdin), and the only native path
(Anthropic Files API) requires an `ANTHROPIC_API_KEY`, which Ari does not use (it runs on a
Claude **subscription OAuth** token). So true in-turn Claude vision is infeasible without
changing the whole LLM backend; every feasible path is "vision model → text". Groq vision was
chosen (reuses the existing `GROQ_API_KEY`).

## 2. Locked decisions

| Decision | Choice | Rationale |
|---|---|---|
| Where media becomes usable | **Skill `InboundTransform` → text**, like voice | LLM is text-only; contract already fits |
| Images | **Groq vision → text** (describe + OCR) | Reuses `GROQ_API_KEY`; native Claude vision infeasible (§1) |
| Documents | **Text extraction** (pypdf / openpyxl / decode) | Claude reads text well; no vision needed |
| Skills, not built-ins | Two first-party skills: `groq_vision`, `documents` | Consistent with `groq_audio`; per-skill enable + credentials |
| Routing | Transforms **self-select by MIME** (`image/*` → vision; pdf/xlsx/csv/text → documents) | Order-independent; each returns `None` when not its type |
| Caption | The Telegram caption is carried as `RawInbound.text` and folded into the returned text | The caption is the user's actual question |
| Image limits | Reject > **20 MB** before calling Groq (Groq's per-image cap); one image per message in v1 | Groq: 20 MB, 3 images max, 2048 tokens/image |
| Extraction size | Truncate extracted document text to a configurable `max_chars` | Keep large PDFs from blowing the context |
| Groq vision model | Configurable in `skill.json` (`vision_model`); **verify the current Groq vision model at implementation** | Groq's vision lineup changes (webfetch showed a qwen vision model; llama‑4 multimodal also exists) |

## 3. Reused skill contract (no change needed)

`domain/skills/models.py` `Attachment.kind` is a plain `str` — new values `"photo"` and
`"document"` are non-breaking. `RawInbound.text` carries the caption. `InboundTransform`
returns `str | None`. `SkillManager.run_inbound` already tries each active skill until one
returns non-`None`. The pipeline from there (`run_inbound → _dispatch → HandleMessage → LLM`)
is unchanged and text-only.

## 4. `groq_vision` skill (images)

`skills/groq_vision/` — `skill.json`, `skill.py` (`GroqVisionClient` + `GroqVisionSkill` +
`build_skill`).

`skill.json`:

```json
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
    "vision_model": "<verify current Groq vision model at impl>",
    "prompt": "Describe esta imagen en detalle y transcribe textualmente cualquier texto visible.",
    "max_bytes": 20971520
  }
}
```

`on_inbound(raw, ctx)`:
- If `raw.attachment` is None or its `mime` does not start with `image/` → return `None` (not
  ours).
- If `len(data) > max_bytes` → log + return `None` (falls through; the gateway sends the
  generic "no pude procesar" — see §7).
- Else POST `https://api.groq.com/openai/v1/chat/completions` (auth `Bearer <GROQ_API_KEY>`)
  with:
  ```json
  {"model": "<vision_model>",
   "messages": [{"role": "user", "content": [
     {"type": "text", "text": "<prompt>"},
     {"type": "image_url", "image_url": {"url": "data:<mime>;base64,<b64>"}}]}]}
  ```
  Parse `choices[0].message.content`. Return it folded with the caption:
  `f"[El usuario envió una imagen{con_texto}. Esto es lo que muestra:]\n{description}"` where
  `con_texto` = ` con el texto: "{raw.text}"` when a caption exists.
- On any HTTP/parse error → log (never the image) + return `None`.

## 5. `documents` skill (PDF / Excel / CSV / text)

`skills/documents/` — `skill.json`, `skill.py` (`DocumentsSkill` + `build_skill`).

`skill.json`: `enabled: true`, `owner_only: true`, `required_secrets: []`,
`hooks: ["inbound_transform"]`, `config: {"max_chars": 20000}`.

`on_inbound(raw, ctx)` selects by MIME/extension and extracts:
- `application/pdf` (or `.pdf`) → `pypdf.PdfReader(BytesIO(data))`, join page texts.
- `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` (or `.xlsx`) →
  `openpyxl.load_workbook(BytesIO(data), read_only=True, data_only=True)`, render each sheet
  as CSV/markdown rows.
- `text/csv` / `text/plain` (or `.csv`/`.txt`) → `data.decode("utf-8", "replace")`.
- Anything else → return `None` (not ours).
- Truncate the extracted text to `max_chars` (append "… (truncado)" when cut). Return it
  folded with the caption and the filename:
  `f"[El usuario envió el archivo «{filename}»{con_texto}. Contenido:]\n{text}"`.
- On extraction error → log + return `None`.

## 6. Gateway (Telegram)

`src/ari/main.py` `_on_message` and a helper in
`src/ari/infrastructure/gateway/telegram_adapter.py`:

- Generalize the voice helper into `media_to_text(update, download, manager, is_owner)` (or a
  parallel `attachment_to_text`) that builds a `RawInbound` from the relevant PTB media and
  runs `manager.run_inbound`. It handles, in `_on_message`, after the text and voice branches:
  - `msg.photo` (a list of `PhotoSize`) → take the **largest** (`msg.photo[-1]`), download,
    `Attachment(kind="photo", mime="image/jpeg", data=..., filename="photo.jpg")`,
    `text=msg.caption or ""`.
  - `msg.document` → download, `Attachment(kind="document", mime=doc.mime_type or "",
    data=..., filename=doc.file_name or "")`, `text=msg.caption or ""`. (A document whose
    mime is `image/*` naturally routes to `groq_vision`.)
- Download bytes exactly like voice (`await media.get_file()` → `download_as_bytearray()`).
- If `run_inbound` returns text → `await _dispatch(update, text)` (from_voice=False, so no TTS).
- If it returns `None` → reuse the existing missing-secret check: if an inbound skill is
  `needs_secrets` (e.g. `groq_vision` without `GROQ_API_KEY`) → send the `vault_web` link
  (already built); else a generic "no pude leer ese archivo/imagen" message.

## 7. Data flow

- **Image:** photo → `_on_message` builds `RawInbound(kind="photo", mime="image/jpeg",
  data, text=caption)` → `run_inbound` → `groq_vision.on_inbound` posts to Groq → description
  text → `_dispatch` → `HandleMessage` → Claude reads it as the user's message.
- **Document:** file → `RawInbound(kind="document", mime, data, filename, text=caption)` →
  `run_inbound` → `documents.on_inbound` extracts text → `_dispatch` → Claude reads it.
- Reactive credential: an image while `GROQ_API_KEY` is missing → `run_inbound` returns None
  and `missing_inbound_secrets` reports `GROQ_API_KEY` → Ari replies with the `/vault` link.

## 8. Configuration (additions)

No new `Settings` fields (skills read their own `config`). New library dependencies (§11).
Skills live under the existing `ARI_SKILLS_DIR`.

## 9. Architecture (new/changed)

```
skills/groq_vision/{skill.json, skill.py}   Groq vision client + inbound transform (images)
skills/documents/{skill.json, skill.py}     pypdf/openpyxl/text extraction inbound transform
src/ari/infrastructure/gateway/telegram_adapter.py  + media/attachment helper for photo/document
src/ari/main.py                             _on_message: photo + document branches → run_inbound → dispatch
pyproject.toml                              + pypdf, + openpyxl
```

`domain/skills/*` unchanged (Attachment.kind gains documented values `"photo"`/`"document"`).

## 10. Error handling

- Unsupported MIME (no skill claims it) → generic "no puedo leer ese tipo de archivo por ahora".
- Image > 20 MB → rejected before Groq; generic message.
- Groq vision HTTP/timeout error → return None → generic message (image never logged).
- Document extraction error (corrupt/encrypted PDF, bad xlsx) → return None → generic message.
- `GROQ_API_KEY` missing when an image arrives → the reactive `vault_web` link (owner only).
- Caption is always preserved so the user's question is never lost.
- Extremely large extracted text → truncated to `max_chars`.

## 11. Dependencies

Add to `pyproject.toml`: `pypdf` (PDF text), `openpyxl` (xlsx). `httpx` (Groq) already present.
No `pandas` (openpyxl + stdlib `csv` suffice). No `python-docx` (Word deferred).

## 12. Testing (pytest + fakes; `python3 -m pytest`; venv has deps)

- `groq_vision`: with a mocked Groq `chat/completions` HTTP (`httpx.MockTransport`) and a tiny
  image byte string, `on_inbound` returns the model's text folded with the caption; a
  non-image mime → `None`; an over-`max_bytes` image → `None` without calling Groq; an HTTP
  error → `None`.
- `documents`: a tiny real PDF (built in-test or a fixture) → extracted text; a tiny xlsx
  (built with openpyxl) → CSV/markdown rows; a `text/csv` and `text/plain` → decoded; a
  non-document mime → `None`; truncation past `max_chars`; a corrupt PDF → `None`.
- Gateway: a fake update with `msg.photo` → `RawInbound(kind="photo", …)` reaches
  `run_inbound`; a fake `msg.document` (pdf) → `RawInbound(kind="document", mime,
  filename)`; caption carried into `RawInbound.text`.
- Routing: an image-mime document goes to vision, a pdf to documents (each skill's
  `on_inbound` returns `None` for the other's mime).
- Loaded via importlib in tests (like `groq_audio`); Groq HTTP mocked; no network.

## 13. Out of scope

- Native in-turn Claude vision (infeasible via the CLI + subscription auth — §1).
- Anthropic API key / Files API path.
- Telegram albums (media groups): one image/document per message in v1.
- Word (`.docx`), PowerPoint, images inside PDFs, audio-in-video.
- Multiple images per message (Groq allows 3; v1 handles the one attachment on the message).
- Persisting/downloading files to disk (bytes are processed in memory).

## 14. Acceptance criteria

1. Owner enables `groq_vision` (`/skill_on groq_vision`); `GROQ_API_KEY` is already loaded, so
   it is `active`. Sending a photo → Ari replies using a description/OCR of the image; a
   caption on the photo is reflected in the reply.
2. With `groq_vision` enabled but `GROQ_API_KEY` missing, sending a photo → Ari replies with
   the `vault_web` `/v/<token>` link (reactive credential path), not a silent drop.
3. Sending a PDF → Ari answers using its extracted text; sending an `.xlsx` → Ari uses the
   sheet contents; sending a `.csv`/`.txt` → same.
4. An unsupported file type → a clear "no puedo leer ese tipo por ahora" message, not silence.
5. An image over 20 MB → rejected with a message, never sent to Groq.
6. All new skill tests pass under the venv; no network in tests (Groq mocked).
