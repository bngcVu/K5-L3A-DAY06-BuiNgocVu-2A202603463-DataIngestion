"""Create blank manual-audit sheets in results/audit/ (you fill in the scores).

    python scripts/make_audit_sheets.py

Sampling rules are fixed here and do not depend on any score:
  coherence: 10 source positions = systematic sample over all content units sorted by
             (document_id, page): index floor((i + 0.5) * N / 10), i = 0..9; the position is
             the text around the middle of that unit. The chunk of A and of B that contains
             it is shown (C uses B's chunks, so its rows repeat B's).
  metadata : the 5 test questions x strategies A/B/C x top-3 (45 rows).
  grounding: the 5 test questions x strategies A/B/C (15 rows), top-3 texts shown together.

Scales (see GUIDE.md): coherence 0 = cut mid-idea / no subject; 1 = partly understandable;
2 = self-contained, idea intact. Grounding 0 = no evidence; 1 = evidence but missing a
condition/context; 2 = enough to answer and trace the source.
"""
import csv
import json
import re
from pathlib import Path

STARTER = Path(__file__).resolve().parents[1]
RESULTS = STARTER / "results"
AUDIT = RESULTS / "audit"
squash = lambda t: re.sub(r"\s+", " ", t).strip()


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_csv(name, header, rows):
    AUDIT.mkdir(parents=True, exist_ok=True)
    with (AUDIT / name).open("w", encoding="utf-8-sig", newline="") as handle:  # opens cleanly in Excel
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    print(f"{name}: {len(rows)} rows")


def main() -> None:
    pages = sorted(read_jsonl(RESULTS / "pages.jsonl"), key=lambda r: (r["document_id"], r["page"]))
    pages = [p for p in pages if not p["metadata"].get("empty")]
    chunks = {s: read_jsonl(RESULTS / f"chunks_{s}.jsonl") for s in "AB"}

    rows = []
    n = len(pages)
    for i in range(10):
        unit = pages[int((i + 0.5) * n / 10)]
        text = squash(unit["text"])
        anchor = text[len(text) // 2:][:60]
        probes = [anchor[k:k + 25] for k in (0, 12, 24, 35)]  # the anchor may straddle a chunk boundary
        for strategy in ("A", "B", "C"):
            source = chunks["A" if strategy == "A" else "B"]
            chunk = next((c for probe in probes for c in source
                          if c["document_id"] == unit["document_id"] and c["page"] == unit["page"]
                          and probe in squash(c["text"])), None)
            rows.append([f"P{i + 1:02d}", unit["document_id"], unit["page"], anchor, strategy,
                         chunk["chunk_id"] if chunk else "NOT FOUND",
                         chunk["text"] if chunk else "", "", "",
                         "same chunk as B" if strategy == "C" else ""])
    write_csv("coherence.csv", ["position", "document_id", "unit", "anchor_text", "strategy", "chunk_id",
                                "chunk_text", "score_0_1_2", "reason", "note"], rows)

    meta_rows, ground_rows = [], []
    for strategy in "ABC":
        for trace in read_jsonl(RESULTS / f"trace_{strategy}_test.jsonl"):
            if trace["split"] != "test":
                continue
            for hit in trace["hits"]:
                m = hit["metadata"]
                meta_rows.append([trace["question_id"], strategy, hit["rank"], hit["chunk_id"],
                                  hit["document_id"], hit["source"], hit["page"], m.get("author"), m.get("paper"),
                                  json.dumps(trace["filter"]) if trace["filter"] else "",
                                  "yes" if trace["filter_applied"] else "no", "", "", "", "", ""])
            ground_rows.append([trace["question_id"], strategy, trace["question"],
                                "\n---\n".join(f"[{h['rank']}] {h['document_id']} unit {h['page']}: {h['text']}"
                                               for h in trace["hits"]), "", ""])
    write_csv("metadata.csv", ["question_id", "strategy", "rank", "chunk_id", "document_id", "source", "unit",
                               "author", "paper", "question_filter", "filter_applied", "source_ok_y_n",
                               "unit_ok_y_n", "author_ok_y_n", "filter_respected_y_n_NA", "note"], meta_rows)
    write_csv("grounding.csv", ["question_id", "strategy", "question", "top3_texts", "score_0_1_2", "reason"],
              ground_rows)


if __name__ == "__main__":
    main()
