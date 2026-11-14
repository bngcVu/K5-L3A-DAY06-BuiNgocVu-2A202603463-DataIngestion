"""Read and normalize source data (PDF and plain text), keeping page traceability."""
import re
import unicodedata
from pathlib import Path
from typing import Any

from .models import DocumentPage

# Zero-width characters and soft hyphen carry no content.
_INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)

# "Chương 1", "Điều 5", "Chapter IV", "Section 2.1" ... (keyword must be followed by a number).
_KEYWORD_RE = re.compile(
    r"^(?i:chương|điều|phần|mục|chapter|section|part|article)\s+(?:\d+[\w.\-]*|[IVXLCDM]+\b)"
)
_NUMBERED_RE = re.compile(r"^\d+(?:\.\d+)*\.?\s+(\S.*)$")


def is_heading(line: str) -> bool:
    """Heuristic heading test for one line: short, no trailing sentence punctuation."""
    line = line.strip()
    if not line or len(line) > 120 or line[-1] in ".,;":
        return False
    if _KEYWORD_RE.match(line):
        return True
    match = _NUMBERED_RE.match(line)
    return bool(match and match.group(1)[0].isupper())


def normalize_text(text: str) -> str:
    """Normalize Unicode (NFC) and whitespace; keep blank lines (paragraphs) and headings.

    - removes zero-width characters, tabs, non-breaking spaces and repeated spaces
    - re-joins words broken by a hyphen at a line end and soft-wrapped lines
    - never joins a heading with its neighbours
    """
    text = unicodedata.normalize("NFC", text).translate(_INVISIBLE)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ").replace("\t", " ")
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)

    out: list[str] = []
    for line in text.split("\n"):
        if out and line and out[-1] and not is_heading(out[-1]) and not is_heading(line):
            previous = out[-1]
            if previous.endswith("-") and len(previous) > 1 and previous[-2].isalpha() and line[0].islower():
                out[-1] = previous[:-1] + line  # "chuyển-\nđổi" -> "chuyểnđổi"
                continue
            if previous[-1] not in ".!?:;" and line[0].islower():
                out[-1] = previous + " " + line  # soft-wrapped line
                continue
        out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def last_heading(text: str) -> str | None:
    """Return the last heading line found in text, or None."""
    found = None
    for line in text.split("\n"):
        if is_heading(line):
            found = line.strip()
    return found


def assign_sections(pages: list[DocumentPage]) -> None:
    """Set metadata['section'] = section in effect at the START of each page.

    The section is carried over from earlier pages of the same document. Headings found
    in the middle of a page are tracked later by the paragraph chunker.
    """
    current: dict[str, str | None] = {}
    for page in pages:
        page.metadata["section"] = current.get(page.document_id)
        heading = last_heading(page.text)
        if heading:
            current[page.document_id] = heading


def load_pdf(path: Path, document_id: str, source: str,
             metadata: dict[str, Any] | None = None) -> list[DocumentPage]:
    """Extract every physical page (1-based) and normalize it.

    Pages with no text get metadata['empty']=True; extraction errors get metadata['error'].
    No page is silently dropped: the caller decides what to exclude and logs it.
    """
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages: list[DocumentPage] = []
    for number, pdf_page in enumerate(reader.pages, start=1):
        meta: dict[str, Any] = dict(metadata or {})
        try:
            text = normalize_text(pdf_page.extract_text() or "")
        except Exception as exc:  # noqa: BLE001 - record any extraction failure
            text = ""
            meta["error"] = f"{type(exc).__name__}: {exc}"
        meta["empty"] = not text.strip()
        pages.append(DocumentPage(document_id, source, number, text, meta))
    return pages


def load_text(path: Path, document_id: str, source: str,
              metadata: dict[str, Any] | None = None, min_chars: int = 1500) -> list[DocumentPage]:
    """Load .txt/.md: split into ordered units of >= min_chars normalized characters.

    Unit numbers (1-based) play the role of pages. A short last unit merges into the previous.
    """
    text = normalize_text(path.read_text(encoding="utf-8"))
    units: list[str] = []
    current: list[str] = []
    length = 0
    for paragraph in re.split(r"\n{2,}", text):
        current.append(paragraph)
        length += len(paragraph)
        if length >= min_chars:
            units.append("\n\n".join(current))
            current, length = [], 0
    if current:
        if units and length < min_chars:
            units[-1] += "\n\n" + "\n\n".join(current)
        else:
            units.append("\n\n".join(current))
    pages = []
    for number, unit in enumerate(units, start=1):
        meta = dict(metadata or {})
        meta["empty"] = not unit.strip()
        meta["unit"] = True
        pages.append(DocumentPage(document_id, source, number, unit, meta))
    return pages


def page_report(pages: list[DocumentPage]) -> dict[str, Any]:
    """Counts for the ingest log: physical, empty, error and content pages."""
    empty = [p.page for p in pages if p.metadata.get("empty")]
    errors = [p.page for p in pages if p.metadata.get("error")]
    content = [p.page for p in pages if not p.metadata.get("empty")]
    return {
        "physical_pages": len(pages),
        "empty_pages": empty,
        "error_pages": errors,
        "content_pages": content,
        "char_count": sum(len(p.text) for p in pages),
    }
