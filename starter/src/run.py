"""Day 06 pipeline entry point. Run from the starter/ folder:

    python -m src.run ingest              # read + normalize -> results/pages.jsonl, ingest_log.json
    python -m src.run chunk               # strategies A and B -> chunks_*.jsonl, chunk_stats.json
    python -m src.run index               # embed + store A and B (C reuses B); run twice to test re-ingest
    python -m src.run eval --split dev    # strategies A, B, C on the dev questions
    python -m src.run eval --split test --final   # ONLY after the configuration is frozen

Inputs (repository root): data/manifest.json and benchmark/questions.json.
"""
import argparse
import json
import os
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .chunking import by_paragraph, fixed_size
from .evaluation import evaluate_query, summarize
from .ingestion import assign_sections, load_pdf, load_text, page_report
from .models import Chunk, DocumentPage

STARTER = Path(__file__).resolve().parents[1]
ROOT = STARTER.parent
RESULTS = STARTER / "results"
MANIFEST = ROOT / "data" / "manifest.json"
BENCHMARK = ROOT / "benchmark" / "questions.json"
MIN_CONTENT_PAGES = 30

# One place for the parameters that define the strategies (copy into experiment_log.csv).
STRATEGIES = {
    "A": {"chunking": "fixed_size", "size": 500, "overlap": 50, "filter": False},
    "B": {"chunking": "by_paragraph", "max_size": 1000, "min_size": 200, "overlap_sentences": 1,
          "filter": False},
    "C": {"chunking": "by_paragraph (same chunks as B)", "filter": True},
}
STORED = ("A", "B")  # C has no chunks of its own: it queries B with a metadata filter


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise SystemExit(f"Missing {path}. Run the previous step first.")
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _update_meta(section: str, values: dict[str, Any]) -> None:
    path = RESULTS / "run_meta.json"
    meta = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    meta[section] = values
    meta["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def load_pages() -> list[DocumentPage]:
    return [DocumentPage(r["document_id"], r["source"], r["page"], r["text"], r["metadata"])
            for r in _read_jsonl(RESULTS / "pages.jsonl")]


def load_chunks(strategy: str) -> list[Chunk]:
    return [Chunk(r["chunk_id"], r["document_id"], r["source"], r["page"], r["text"], r["metadata"])
            for r in _read_jsonl(RESULTS / f"chunks_{strategy}.jsonl")]


def build_chunks(pages: list[DocumentPage]) -> dict[str, list[Chunk]]:
    a, b = STRATEGIES["A"], STRATEGIES["B"]
    chunks: dict[str, list[Chunk]] = {"A": [], "B": []}
    for page in pages:
        chunks["A"].extend(fixed_size(page, a["size"], a["overlap"]))
        chunks["B"].extend(by_paragraph(page, b["max_size"], b["min_size"], b["overlap_sentences"]))
    return chunks


def cmd_ingest() -> None:
    if not MANIFEST.exists():
        raise SystemExit(f"Missing {MANIFEST}. Create the manifest first (see myDocs/Implement.md).")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest_docs = {d["source_id"]: d for d in manifest["documents"]}
    pages: list[DocumentPage] = []
    log: dict[str, Any] = {"documents": [], "errors": []}
    for doc in manifest["documents"]:
        source_id = doc["source_id"]
        path = ROOT / doc["path"]
        if not path.exists():
            raise SystemExit(f"Source file not found: {path}")
        meta = dict(doc.get("metadata", {}))  # e.g. {"year": "2023"}: values stated in the source
        loader = load_pdf if path.suffix.lower() == ".pdf" else load_text
        doc_pages = loader(path, source_id, doc.get("title", source_id), meta)
        excluded = set(doc.get("exclude_pages", []))
        kept = [p for p in doc_pages if p.page not in excluded]
        report = page_report(kept)
        report.update(source_id=source_id, excluded_pages=sorted(excluded),
                      physical_pages_in_file=len(doc_pages))
        log["documents"].append(report)
        # write the measured counts back into the manifest (evidence for the page count)
        manifest_docs[source_id].update(content_pages=report["content_pages"],
                                        physical_pages=report["physical_pages"],
                                        char_count=report["char_count"])
        for page in kept:
            if page.metadata.get("error"):
                log["errors"].append({"source_id": source_id, "page": page.page,
                                      "error": page.metadata["error"]})
        pages.extend(kept)
    assign_sections(pages)
    total = sum(len(d["content_pages"]) for d in log["documents"])
    log["total_content_pages"] = total
    log["meets_minimum_30_pages"] = total >= MIN_CONTENT_PAGES
    manifest["total_content_pages"] = total
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_jsonl(RESULTS / "pages.jsonl",
                 [{"document_id": p.document_id, "source": p.source, "page": p.page,
                   "text": p.text, "metadata": p.metadata} for p in pages])
    (RESULTS / "ingest_log.json").write_text(json.dumps(log, ensure_ascii=False, indent=2),
                                             encoding="utf-8")
    print(f"pages kept: {len(pages)} | content pages: {total} | "
          f"empty: {sum(len(d['empty_pages']) for d in log['documents'])} | errors: {len(log['errors'])}")
    if total < MIN_CONTENT_PAGES:
        print(f"WARNING: only {total} content pages; the lab requires at least {MIN_CONTENT_PAGES}.")


def cmd_chunk() -> None:
    pages = [p for p in load_pages() if not p.metadata.get("empty")]
    chunks = build_chunks(pages)
    stats: dict[str, Any] = {}
    for strategy, items in chunks.items():
        _write_jsonl(RESULTS / f"chunks_{strategy}.jsonl",
                     [{"chunk_id": c.chunk_id, "document_id": c.document_id, "source": c.source,
                       "page": c.page, "text": c.text, "metadata": c.metadata} for c in items])
        lengths = [len(c.text) for c in items] or [0]
        stats[strategy] = {"params": STRATEGIES[strategy], "chunks": len(items),
                           "min_chars": min(lengths), "median_chars": statistics.median(lengths),
                           "max_chars": max(lengths), "total_chars": sum(lengths),
                           "approx_tokens": sum(lengths) // 4}
        print(f"{strategy}: {stats[strategy]}")
    (RESULTS / "chunk_stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2),
                                              encoding="utf-8")


def _store_and_embedder():
    from .embeddings import MyEmbedder
    from .store import PgVectorStore
    load_dotenv(STARTER / ".env")
    embedder = MyEmbedder.from_env()
    url = os.getenv("DATABASE_URL", "")
    store = PgVectorStore(url, embedder.dimensions)
    store.create_schema()
    return embedder, store


def cmd_index() -> None:
    import sentence_transformers
    embedder, store = _store_and_embedder()
    info: dict[str, Any] = {"model": embedder.model_name, "dimensions": embedder.dimensions,
                            "sentence_transformers": sentence_transformers.__version__,
                            "cost_usd": 0, "strategies": {}}
    for strategy in STORED:
        chunks = load_chunks(strategy)
        before = store.count(strategy)
        started = time.perf_counter()
        vectors = embedder.embed_documents([c.text for c in chunks])
        embedded = time.perf_counter()
        store.upsert(strategy, chunks, vectors)
        done = time.perf_counter()
        after = store.count(strategy)
        info["strategies"][strategy] = {
            "chunks": len(chunks), "rows_before": before, "rows_after": after,
            "embed_seconds": round(embedded - started, 2), "store_seconds": round(done - embedded, 2)}
        print(f"{strategy}: {len(chunks)} chunks | rows {before} -> {after} | "
              f"embed {embedded - started:.1f}s | store {done - embedded:.1f}s")
        if after != len(chunks):
            print(f"WARNING: rows ({after}) differ from chunks ({len(chunks)})")
    _update_meta("index", info)


def cmd_eval(split: str, final: bool) -> None:
    if split == "test" and not final:
        raise SystemExit("Refusing to run the TEST split without --final. Freeze the strategy "
                         "choice on dev first (see myDocs/Implement.md, section 9).")
    data = json.loads(BENCHMARK.read_text(encoding="utf-8")) if BENCHMARK.exists() else None
    if data is None:
        raise SystemExit(f"Missing {BENCHMARK}.")
    questions = [q for q in (data["questions"] if isinstance(data, dict) else data)
                 if q["split"] == split]
    if not questions:
        raise SystemExit(f"No questions with split={split!r}.")
    embedder, store = _store_and_embedder()
    query_vectors = {q["id"]: embedder.embed_query(q["question"]) for q in questions}
    traces: list[dict[str, Any]] = []
    timing: dict[str, float] = {}
    for strategy in STRATEGIES:
        stored = "A" if strategy == "A" else "B"  # C queries B's chunks
        rows = []
        started = time.perf_counter()
        for q in questions:
            filters = q.get("filter") if strategy == "C" else None
            hits = store.search(stored, query_vectors[q["id"]], 3, filters)
            trace = evaluate_query(q, hits)
            trace.update(strategy=strategy, filter_applied=bool(filters))
            rows.append(trace)
        timing[strategy] = round((time.perf_counter() - started) / len(questions) * 1000, 1)
        _write_jsonl(RESULTS / f"trace_{strategy}_{split}.jsonl", rows)
        traces.extend(rows)
    summary = summarize(traces)
    lines = ["strategy,split,n,hit_at_1,hit_at_3,ms_per_query"]
    for strategy, by_split in summary.items():
        for sp, v in by_split.items():
            lines.append(f"{strategy},{sp},{v['n']},{v['hit_at_1_text']},{v['hit_at_3_text']},"
                         f"{timing[strategy]}")
            print(f"{strategy} {sp}: Hit@1 {v['hit_at_1_text']} | Evidence Hit@3 {v['hit_at_3_text']}"
                  f" | {timing[strategy]} ms/query")
    (RESULTS / f"summary_{split}.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _update_meta(f"eval_{split}", {"questions": len(questions), "ms_per_query": timing,
                                   "model": embedder.model_name, "top_k": 3})


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m src.run", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ingest")
    sub.add_parser("chunk")
    sub.add_parser("index")
    ev = sub.add_parser("eval")
    ev.add_argument("--split", choices=["dev", "test"], required=True)
    ev.add_argument("--final", action="store_true", help="required for the test split")
    args = parser.parse_args()
    if args.command == "ingest":
        cmd_ingest()
    elif args.command == "chunk":
        cmd_chunk()
    elif args.command == "index":
        cmd_index()
    else:
        cmd_eval(args.split, args.final)


if __name__ == "__main__":
    main()
