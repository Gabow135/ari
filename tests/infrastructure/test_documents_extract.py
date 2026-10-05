# tests/infrastructure/test_documents_extract.py
"""Tests for the shared document extractor.

reportlab is NOT installed in this project; positive PDF text extraction is
covered via a hand-crafted minimal PDF bytes literal with a real text layer
(see _PDF_WITH_TEXT).  The blank-page (empty → None) and corrupt-bytes paths
are also covered.
"""
import io

import openpyxl
import pypdf
import pytest

from ari.infrastructure.workspace.documents import extract_document


# ---------------------------------------------------------------------------
# static fixtures
# ---------------------------------------------------------------------------

# Minimal valid PDF with a real text layer — hand-crafted with correct xref
# offsets so pypdf can extract the content stream without any third-party
# generator.  Verified against pypdf 6.x: pages[0].extract_text() == 'Total 123.45'.
_PDF_WITH_TEXT = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
    b"/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>endobj\n"
    b"4 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
    b"5 0 obj<</Length 44>>stream\n"
    b"BT /F1 24 Tf 72 700 Td (Total 123.45) Tj ET\n"
    b"endstream endobj\n"
    b"xref\n"
    b"0 6\n"
    b"0000000000 65535 f \n"
    b"0000000009 00000 n \n"
    b"0000000052 00000 n \n"
    b"0000000101 00000 n \n"
    b"0000000211 00000 n \n"
    b"0000000272 00000 n \n"
    b"trailer<</Size 6/Root 1 0 R>>\n"
    b"startxref\n"
    b"361\n"
    b"%%EOF\n"
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _xlsx_bytes(title: str = "Sheet1", rows: list | None = None) -> bytes:
    if rows is None:
        rows = [["name", "score"], ["Ana", 10], ["Bob", 20]]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _blank_pdf_bytes() -> bytes:
    w = pypdf.PdfWriter()
    w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# XLSX extraction
# ---------------------------------------------------------------------------

def test_xlsx_extracted_by_mime():
    data = _xlsx_bytes("Data", [["x", "y"], [1, 2]])
    text = extract_document(
        data,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    assert text is not None
    assert "# Data" in text
    assert "x,y" in text
    assert "1,2" in text


def test_xlsx_extracted_by_filename_extension():
    data = _xlsx_bytes("Hoja1", [["a", "b"], [3, 4]])
    text = extract_document(data, filename="report.xlsx")
    assert text is not None
    assert "# Hoja1" in text
    assert "a,b" in text
    assert "3,4" in text


# ---------------------------------------------------------------------------
# CSV / TXT
# ---------------------------------------------------------------------------

def test_csv_decoded_by_mime():
    data = b"name,age\nAna,30\nBob,25"
    text = extract_document(data, mime="text/csv")
    assert text == "name,age\nAna,30\nBob,25"


def test_txt_decoded_by_mime():
    data = b"hello world"
    text = extract_document(data, mime="text/plain")
    assert text == "hello world"


def test_csv_decoded_by_extension():
    data = b"a,b,c"
    text = extract_document(data, filename="data.csv")
    assert text == "a,b,c"


def test_txt_decoded_by_extension():
    data = b"hello"
    text = extract_document(data, filename="notes.TXT")
    assert text == "hello"


# ---------------------------------------------------------------------------
# PDF path — blank page (no extractable text → None)
# ---------------------------------------------------------------------------

def test_blank_pdf_returns_none():
    data = _blank_pdf_bytes()
    text = extract_document(data, mime="application/pdf")
    assert text is None


def test_blank_pdf_by_extension():
    data = _blank_pdf_bytes()
    text = extract_document(data, filename="doc.pdf")
    assert text is None


def test_pdf_with_text_is_extracted():
    out = extract_document(_PDF_WITH_TEXT, filename="factura.pdf")
    assert out is not None and "123.45" in out


# ---------------------------------------------------------------------------
# Unsupported type → None
# ---------------------------------------------------------------------------

def test_unsupported_mime_returns_none():
    assert extract_document(b"\x89PNG\r\n", mime="image/png") is None


def test_unsupported_extension_returns_none():
    assert extract_document(b"data", filename="archive.zip") is None


def test_no_mime_no_filename_returns_none():
    assert extract_document(b"data") is None


# ---------------------------------------------------------------------------
# Empty text → None
# ---------------------------------------------------------------------------

def test_whitespace_only_returns_none():
    assert extract_document(b"   \n\t  ", mime="text/plain") is None


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------

def test_truncation_appends_marker():
    data = b"0123456789ABCDEFG"
    text = extract_document(data, mime="text/plain", max_chars=10)
    assert text is not None
    assert text.startswith("0123456789")
    assert "\n… (truncado)" in text


def test_no_truncation_when_within_limit():
    data = b"short"
    text = extract_document(data, mime="text/plain", max_chars=100)
    assert text == "short"
    assert "truncado" not in text


# ---------------------------------------------------------------------------
# Mime takes precedence over extension
# ---------------------------------------------------------------------------

def test_mime_wins_over_extension():
    # .txt extension but mime says CSV — both decode the same way, fine.
    # Use xlsx bytes with txt extension to confirm mime wins: xlsx mime → xlsx branch
    data = _xlsx_bytes("Priority", [["ok", "yes"]])
    text = extract_document(
        data,
        filename="not_an_xlsx.txt",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    assert text is not None
    assert "# Priority" in text
