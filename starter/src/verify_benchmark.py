"""Check benchmark/questions.json BEFORE running experiments.

    python -m src.verify_benchmark

Checks: required fields, unique ids, >=20 questions, >=15 dev / >=5 test, >=3 filter questions
(>=1 in test), and that every evidence_quote really appears on its gold page after normalization.
"""
import json
import sys
from collections import Counter

from .evaluation import gold_entries, normalize_for_match
from .run import BENCHMARK, RESULTS, _read_jsonl

REQUIRED = ["id", "split", "question", "expected_answer", "gold"]


def main() -> int:
    if not BENCHMARK.exists():
        print(f"Missing {BENCHMARK}")
        return 1
    data = json.loads(BENCHMARK.read_text(encoding="utf-8"))
    questions = data["questions"] if isinstance(data, dict) else data
    pages = {(r["document_id"], str(r["page"])): normalize_for_match(r["text"])
             for r in _read_jsonl(RESULTS / "pages.jsonl")}
    problems: list[str] = []

    ids = Counter(q.get("id") for q in questions)
    problems += [f"duplicate id: {i}" for i, n in ids.items() if n > 1]
    splits = Counter(q.get("split") for q in questions)
    filtered = [q for q in questions if q.get("filter")]
    if len(questions) < 20:
        problems.append(f"only {len(questions)} questions (need >= 20)")
    if splits["dev"] < 15 or splits["test"] < 5:
        problems.append(f"split dev={splits['dev']} test={splits['test']} (need >= 15 / >= 5)")
    if len(filtered) < 3 or not any(q["split"] == "test" for q in filtered):
        problems.append(f"filter questions: {len(filtered)} (need >= 3, at least 1 in test)")

    for q in questions:
        missing = [f for f in REQUIRED if not q.get(f)]
        if missing:
            problems.append(f"{q.get('id')}: missing {missing}")
        entries = gold_entries(q)
        if not entries or not any(e["quotes"] for e in entries):
            problems.append(f"{q.get('id')}: no evidence_quote")
        for entry in entries:
            page_text = pages.get((entry["document_id"], str(entry["page"])))
            if page_text is None:
                problems.append(f"{q.get('id')}: gold page not found {entry['document_id']} p.{entry['page']}")
                continue
            for quote in entry["quotes"]:
                if normalize_for_match(quote) not in page_text:
                    problems.append(f"{q.get('id')}: evidence not on {entry['document_id']} "
                                    f"p.{entry['page']}: {quote[:60]!r}")
    print(f"questions: {len(questions)} | split: {dict(splits)} | with filter: {len(filtered)}")
    for line in problems:
        print("PROBLEM:", line)
    print("OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
