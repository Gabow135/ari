# src/ari/infrastructure/workspace/documents.py
"""Shared document text extractor.

Extracts plain text from PDF, XLSX, CSV, and TXT byte payloads.
Type is resolved by MIME first, then by filename extension (case-insensitive).
Returns None for unsupported types and for documents with no extractable text.
Corrupt or encrypted documents propagate their exception to the caller.
"""
from __future__ import annotations

import io

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def extract_document(
    data: bytes,
    filename: str = "",
    mime: str = "",
    max_chars: int = 20_000,
) -> str | None:
    """Extract text from *data* and return it, or None if unsupported / empty.

    Resolution order:
      1. MIME type (when non-empty).
      2. Filename extension (case-insensitive).

    Args:
        data: Raw file bytes.
        filename: Original file name (used for extension fallback).
        mime: MIME type (takes precedence over extension when non-empty).
        max_chars: Maximum characters to return; longer text is truncated
                   and ``"\\n… (truncado)"`` is appended.

    Returns:
        Extracted text, or None when the type is unsupported or the result
        is empty after stripping.

    Raises:
        Any exception raised by pypdf or openpyxl for corrupt/encrypted files.
    """
    m = (mime or "").lower()
    ext = (filename or "").lower()

    if m == "application/pdf" or (not m and ext.endswith(".pdf")):
        text = _pdf(data)
    elif m == _XLSX_MIME or (not m and ext.endswith(".xlsx")):
        text = _xlsx(data)
    elif m in ("text/csv", "text/plain") or (not m and (ext.endswith(".csv") or ext.endswith(".txt"))):
        text = data.decode("utf-8", "replace")
    else:
        return None

    text = (text or "").strip()
    if not text:
        return None

    if len(text) > max_chars:
        text = text[:max_chars] + "\n… (truncado)"

    return text


def _pdf(data: bytes) -> str:
    import pypdf
    reader = pypdf.PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _xlsx(data: bytes) -> str:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    lines: list[str] = []
    for ws in wb.worksheets:
        lines.append(f"# {ws.title}")
        for row in ws.iter_rows(values_only=True):
            lines.append(",".join("" if c is None else str(c) for c in row))
    return "\n".join(lines)
