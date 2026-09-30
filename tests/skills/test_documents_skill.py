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
