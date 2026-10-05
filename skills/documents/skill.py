# skills/documents/skill.py
from ari.infrastructure.workspace.documents import extract_document


class DocumentsSkill:
    def __init__(self, config):
        self._cfg = config

    async def on_inbound(self, raw, ctx):
        att = raw.attachment
        if att is None:
            return None
        try:
            text = extract_document(
                att.data,
                filename=att.filename or "",
                mime=att.mime or "",
                max_chars=self._cfg.get("max_chars", 20000),
            )
        except Exception as exc:  # corrupt/encrypted/unreadable — never crash the turn
            ctx.log.warning("documents: extraction failed for %s: %s", att.filename or att.mime or "documento", exc)
            return None
        if not text:
            return None
        caption = (raw.text or "").strip()
        con = f' con el texto: "{caption}"' if caption else ""
        fname = att.filename or "archivo"
        return f"[El usuario envió el archivo «{fname}»{con}. Contenido:]\n{text}"


def build_skill(config):
    return DocumentsSkill(config)
