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
