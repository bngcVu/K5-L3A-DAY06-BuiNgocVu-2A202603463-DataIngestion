"""Fixed-size (strategy A) and structure-aware (strategy B/C) chunking.

Both functions work on ONE page, so every chunk keeps exactly one `page` for traceability.
"""
import hashlib
import re
from typing import Any

from .ingestion import is_heading
from .models import Chunk, DocumentPage

_NON_INHERITED = {"empty", "error", "section", "unit"}
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _chunk_id(strategy: str, document_id: str, page: int, index: int, text: str) -> str:
    """Stable ID: same content at the same place -> same ID (re-ingest never duplicates)."""
    raw = f"{strategy}|{document_id}|{page}|{index}|{text}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _make_chunk(page: DocumentPage, strategy: str, index: int, text: str, section: str | None) -> Chunk:
    metadata: dict[str, Any] = {k: v for k, v in page.metadata.items() if k not in _NON_INHERITED}
    metadata.update(strategy=strategy, chunk_index=index, char_count=len(text), section=section)
    return Chunk(_chunk_id(strategy, page.document_id, page.page, index, text),
                 page.document_id, page.source, page.page, text, metadata)


def _windows(text: str, size: int, overlap: int) -> list[str]:
    """Sliding windows of ~size characters; cut at whitespace so words are never split."""
    if size <= 0 or not 0 <= overlap < size:
        raise ValueError("need size > 0 and 0 <= overlap < size")
    length = len(text)
    pieces: list[str] = []
    start = 0
    while start < length:
        end = min(start + size, length)
        if end < length:
            cut = max(text.rfind(" ", start + size // 2, end), text.rfind("\n", start + size // 2, end))
            if cut != -1:
                end = cut
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= length:
            break
        nxt = end - overlap
        if nxt <= start:
            nxt = end
        if nxt > 0 and not text[nxt - 1].isspace():  # do not start the next window mid-word
            match = re.compile(r"\s").search(text, nxt)
            nxt = match.end() if match else length
        start = min(nxt, end)
    return pieces


def fixed_size(page: DocumentPage, size: int = 500, overlap: int = 50) -> list[Chunk]:
    """Strategy A: overlapping character windows. Section = section at the start of the page."""
    section = page.metadata.get("section")
    return [_make_chunk(page, "A", i, piece, section)
            for i, piece in enumerate(_windows(page.text, size, overlap))]


def _units(page: DocumentPage) -> list[tuple[bool, str, str | None]]:
    """Split a page into (is_heading, text, section_in_effect) units.

    Blank lines separate paragraphs; inside a block every heading line is its own unit and
    consecutive non-heading lines are joined into one paragraph.
    """
    section = page.metadata.get("section")
    units: list[tuple[bool, str, str | None]] = []
    for block in re.split(r"\n{2,}", page.text):
        paragraph: list[str] = []
        for line in block.split("\n"):
            line = line.strip()
            if not line:
                continue
            if is_heading(line):
                if paragraph:
                    units.append((False, " ".join(paragraph), section))
                    paragraph = []
                section = line
                units.append((True, line, section))
            else:
                paragraph.append(line)
        if paragraph:
            units.append((False, " ".join(paragraph), section))
    return units


def _split_long(text: str, max_size: int, overlap_sentences: int) -> list[str]:
    """Split an oversized block at sentence boundaries, repeating the last sentence(s)."""
    sentences: list[str] = []
    for sentence in _SENTENCE_SPLIT.split(text):
        if len(sentence) > max_size:
            sentences.extend(_windows(sentence, max_size, 0))
        elif sentence:
            sentences.append(sentence)
    pieces: list[str] = []
    current: list[str] = []
    for sentence in sentences:
        if current and len(" ".join(current + [sentence])) > max_size:
            pieces.append(" ".join(current))
            current = current[-overlap_sentences:] if overlap_sentences else []
            if current and len(" ".join(current + [sentence])) > max_size:
                current = []
        current.append(sentence)
    if current:
        pieces.append(" ".join(current))
    return pieces


def by_paragraph(page: DocumentPage, max_size: int = 1000, min_size: int = 200,
                 overlap_sentences: int = 1) -> list[Chunk]:
    """Strategy B (also used by C): pack whole paragraphs under their heading.

    - a heading is always kept with the paragraph that follows it
    - paragraphs are packed up to max_size and never merged across sections
    - oversized paragraphs are split at sentence boundaries (last sentence repeated)
    - a chunk shorter than min_size merges into the previous one (same section) if it fits;
      a tiny tail (< min_size/2) may overshoot max_size by at most min_size/2
    - a short first chunk merges forward into the second one if it fits
    """
    pieces: list[tuple[str, str | None]] = []
    buffer: list[str] = []
    buffer_section: str | None = None
    pending: list[str] = []

    def flush() -> None:
        nonlocal buffer
        if buffer:
            pieces.append(("\n\n".join(buffer), buffer_section))
            buffer = []

    for is_head, text, section in _units(page):
        if is_head:
            pending.append(text)
            continue
        block = "\n".join(pending + [text])
        pending = []
        if len(block) > max_size:
            flush()
            for part in _split_long(block, max_size, overlap_sentences):
                pieces.append((part, section))
            continue
        if buffer and (section != buffer_section or len("\n\n".join(buffer + [block])) > max_size):
            flush()
        if not buffer:
            buffer_section = section
        buffer.append(block)
    if pending:  # trailing heading(s) with no paragraph after them on this page
        tail = "\n".join(pending)
        if buffer:
            buffer.append(tail)
        else:
            pieces.append((tail, page.metadata.get("section")))
    flush()

    merged: list[tuple[str, str | None]] = []
    for text, section in pieces:
        if merged and len(text) < min_size and merged[-1][1] == section:
            # a tiny tail (< min_size/2, e.g. a signature or footnote) may exceed max_size slightly
            limit = max_size + (min_size // 2 if len(text) < min_size // 2 else 0)
            if len(merged[-1][0]) + 2 + len(text) <= limit:
                merged[-1] = (merged[-1][0] + "\n\n" + text, section)
                continue
        merged.append((text, section))
    # a short leading piece (e.g. title + salutation) merges forward into the next one
    if len(merged) > 1 and len(merged[0][0]) < min_size and merged[0][1] == merged[1][1] \
            and len(merged[0][0]) + 2 + len(merged[1][0]) <= max_size:
        merged[1] = (merged[0][0] + "\n\n" + merged[1][0], merged[1][1])
        merged.pop(0)
    return [_make_chunk(page, "B", i, text, section) for i, (text, section) in enumerate(merged)]
