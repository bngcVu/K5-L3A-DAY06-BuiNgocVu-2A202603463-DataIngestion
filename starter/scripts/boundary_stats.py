"""Descriptive check: how many chunks start and end on a sentence boundary?

    python scripts/boundary_stats.py

Heuristic only (not a quality score and not a substitute for the manual coherence labels):
  starts clean = first character is an uppercase letter, digit or opening quote/paren
  ends clean   = text ends with . ! ? (optionally followed by quote/paren/footnote mark)
A mid-sentence proper noun counts as a clean start, so the numbers are an upper bound.
"""
import json
import re
from pathlib import Path

RESULTS = Path(__file__).resolve().parents[1] / "results"


def starts_clean(text: str) -> bool:
    text = text.lstrip()
    return bool(text) and (text[0].isupper() or text[0].isdigit() or text[0] in "\"'(")


def ends_clean(text: str) -> bool:
    return bool(re.search(r"[.!?][\"')\]]*(\(\d+\))?[\"')\]]*$", text.rstrip()))


for strategy in "AB":
    path = RESULTS / f"chunks_{strategy}.jsonl"
    chunks = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    n = len(chunks)
    start = sum(starts_clean(c["text"]) for c in chunks)
    end = sum(ends_clean(c["text"]) for c in chunks)
    both = sum(starts_clean(c["text"]) and ends_clean(c["text"]) for c in chunks)
    print(f"{strategy}: chunks {n} | start clean {start} ({start / n:.0%}) | end clean {end} ({end / n:.0%}) "
          f"| both clean {both} ({both / n:.0%})")
