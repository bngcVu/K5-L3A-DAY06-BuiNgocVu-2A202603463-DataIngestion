"""Split the Project Gutenberg Federalist Papers (#1404) into one text file per paper and
write data/manifest.json.

    python scripts/prepare_corpus.py

Source (public domain, original text 1787-1788):
    https://www.gutenberg.org/cache/epub/1404/pg1404.txt  -> starter/data/raw/pg1404.txt
Only papers whose header names ONE author (Hamilton, Madison or Jay) are used: the author
is the metadata filter field and its value is stated in the source, never guessed.
"""
import json
import re
from pathlib import Path

STARTER = Path(__file__).resolve().parents[1]
ROOT = STARTER.parent
RAW = STARTER / "data" / "raw" / "pg1404.txt"
OUT = STARTER / "data" / "federalist"
MANIFEST = ROOT / "data" / "manifest.json"

SELECTED = [1, 6, 9, 11, 15, 23, 30, 31, 35, 65, 68, 70, 72, 78, 84,        # Hamilton
            10, 14, 37, 38, 39, 41, 43, 45, 47, 48, 51, 62,                  # Madison
            2, 3, 4, 5, 64]                                                  # Jay
AUTHORS = {"HAMILTON": "Hamilton", "MADISON": "Madison", "JAY": "Jay"}


def main() -> None:
    raw = RAW.read_text(encoding="utf-8")
    body = raw.split("*** START OF THE PROJECT GUTENBERG EBOOK THE FEDERALIST PAPERS ***", 1)[1]
    body = body.split("*** END OF THE PROJECT GUTENBERG EBOOK THE FEDERALIST PAPERS ***", 1)[0]
    marks = list(re.finditer(r"^FEDERALIST No\. (\d+)[ \t]*$", body, re.M))
    OUT.mkdir(parents=True, exist_ok=True)
    documents, skipped = [], []
    for i, mark in enumerate(marks):
        number = int(mark.group(1))
        if number not in SELECTED:
            continue
        end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        lines = body[mark.end():end].replace("\r", "").strip("\n").split("\n")
        # title = lines up to the first blank line; author = first all-caps line after it
        title_lines = []
        position = 0
        while position < len(lines) and not lines[position].strip():
            position += 1
        while position < len(lines) and lines[position].strip():
            title_lines.append(lines[position].strip())
            position += 1
        author_line = next((j for j in range(position, min(position + 10, len(lines)))
                            if re.fullmatch(r"[A-Z ]+", lines[j].strip() or "x")), None)
        author = AUTHORS.get(lines[author_line].strip()) if author_line is not None else None
        if author is None:
            skipped.append(number)
            continue
        text = "\n".join(lines[author_line + 1:]).strip()
        title = " ".join(title_lines)
        document_id = f"fed{number:02d}"
        (OUT / f"{document_id}.txt").write_text(
            f"Federalist No. {number}: {title}\n\n{text}\n", encoding="utf-8")
        documents.append({
            "source_id": document_id,
            "title": f"The Federalist No. {number}: {title}",
            "path": f"starter/data/federalist/{document_id}.txt",
            "metadata": {"author": author, "paper": number},
            "exclude_pages": [],
            "license_or_permission": "Public domain (1787-1788 text); Project Gutenberg eBook #1404, "
                                     "redistributed unmodified apart from splitting by paper",
        })
    manifest = {
        "corpus": "The Federalist Papers (selected single-author papers)",
        "source_url": "https://www.gutenberg.org/cache/epub/1404/pg1404.txt",
        "accessed_at": "2026-11-13",
        "snapshot": "Project Gutenberg eBook #1404, release date 1998-07-01",
        "filter_field": "author (values stated in each paper's header: Hamilton, Madison, Jay)",
        "counting_rule": "Plain text has no pages: each normalized unit of >= 1500 characters "
                         "(consecutive paragraphs of one paper) counts as one page; a short last unit "
                         "merges into the previous one. Papers are independent source documents.",
        "documents": documents,
    }
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    by_author = {}
    for d in documents:
        by_author.setdefault(d["metadata"]["author"], []).append(d["metadata"]["paper"])
    print(f"papers written: {len(documents)} -> {OUT}")
    print(f"by author: {by_author}")
    print(f"skipped (no single author in header): {skipped}")
    print(f"manifest: {MANIFEST}")


if __name__ == "__main__":
    main()
