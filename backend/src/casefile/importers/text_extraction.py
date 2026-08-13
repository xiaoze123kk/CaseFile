"""Plain-text extraction for Path C imported documents."""

from __future__ import annotations

from io import BytesIO
from typing import Final

from docx import Document
from pypdf import PdfReader

MAX_FILE_BYTES: Final = 5 * 1024 * 1024
MAX_PDF_PAGES: Final = 100
_SUPPORTED = (".txt", ".md", ".markdown", ".docx", ".pdf")


class ExtractionError(ValueError):
    """Document cannot be converted to plain text."""


def extract_text(filename: str, data: bytes) -> str:
    if len(data) > MAX_FILE_BYTES:
        raise ExtractionError("文件超过 5MB 上限。")
    normalized = filename.lower()
    if not normalized.endswith(_SUPPORTED):
        raise ExtractionError("不支持该文件格式，仅支持纯文本、Markdown、DOCX 与文本型 PDF。")
    if normalized.endswith(".pdf"):
        return _pdf_text(data)
    if normalized.endswith(".docx"):
        return _docx_text(data)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ExtractionError("文本文件需为 UTF-8 编码。") from error


def _docx_text(data: bytes) -> str:
    try:
        document = Document(BytesIO(data))
    except Exception as error:
        raise ExtractionError("DOCX 文件无法解析。") from error
    return "\n".join(paragraph.text for paragraph in document.paragraphs).strip()


def _pdf_text(data: bytes) -> str:
    try:
        reader = PdfReader(BytesIO(data))
    except Exception as error:
        raise ExtractionError("PDF 文件无法解析，请确认是文本型 PDF。") from error
    if len(reader.pages) > MAX_PDF_PAGES:
        raise ExtractionError(f"PDF 超过 {MAX_PDF_PAGES} 页上限。")
    parts = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(parts).strip()


def split_blocks(text: str) -> list[str]:
    """Split extracted text into numbered non-empty blocks separated by blank lines."""
    return [block.strip() for block in text.split("\n\n") if block.strip()]
