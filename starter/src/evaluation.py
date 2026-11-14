"""Evaluate retrieved evidence (Hit@1 / Evidence Hit@3) and keep per-query traces.

This only measures retrieval. Chunk coherence, metadata usefulness and grounding quality
are labelled by hand (see results/audit/); they are never inferred from string matching.
"""
import re
import unicodedata
from typing import Any


def normalize_for_match(text: str) -> str:
    """Case/space-insensitive form used to find an evidence quote inside a chunk."""
    text = unicodedata.normalize("NFC", text).casefold()
    return re.sub(r"\s+", " ", text).strip()


def _quotes(value: Any) -> list[str]:
    if not value:
        return []
    return [value] if isinstance(value, str) else list(value)


def gold_entries(question: dict[str, Any]) -> list[dict[str, Any]]:
    """Every accepted gold location: {document_id, page, quotes}.

    `gold` may list several alternative (document, page) locations; each may carry its own
    `evidence_quote` (str, or list = ALL parts must appear), else the question-level quote is used.
    """
    entries = []
    for gold in question.get("gold") or []:
        quotes = _quotes(gold.get("evidence_quote")) or _quotes(question.get("evidence_quote"))
        entries.append({"document_id": gold.get("source_id") or gold.get("document_id"),
                        "page": gold.get("page"), "quotes": quotes})
    return entries


def evaluate_query(question: dict[str, Any], hits: list[dict[str, Any]]) -> dict[str, Any]:
    """A hit = chunk from an accepted (document, page) that contains ALL gold evidence quotes."""
    entries = gold_entries(question)
    ranked = []
    for position, hit in enumerate(hits, start=1):
        text = normalize_for_match(hit.get("text", ""))
        page_match = any(str(e["document_id"]) == str(hit.get("document_id"))
                         and str(e["page"]) == str(hit.get("page")) for e in entries)
        is_hit = any(
            str(e["document_id"]) == str(hit.get("document_id")) and str(e["page"]) == str(hit.get("page"))
            and e["quotes"] and all(normalize_for_match(q) in text for q in e["quotes"])
            for e in entries)
        ranked.append({"rank": hit.get("rank", position), "chunk_id": hit.get("chunk_id"),
                       "score": hit.get("score"), "document_id": hit.get("document_id"),
                       "source": hit.get("source"), "page": hit.get("page"),
                       "metadata": hit.get("metadata", {}), "text": hit.get("text"),
                       "page_match": page_match, "is_hit": is_hit})
    top3 = ranked[:3]
    hit_at_1 = bool(top3 and top3[0]["is_hit"])
    hit_at_3 = any(h["is_hit"] for h in top3)
    first = next((h["rank"] for h in top3 if h["is_hit"]), None)
    return {
        "question_id": question.get("id"), "split": question.get("split"),
        "type": question.get("type"), "question": question.get("question"),
        "filter": question.get("filter"),
        "hit_at_1": hit_at_1, "hit_at_3": hit_at_3, "first_hit_rank": first,
        # right page retrieved but no chunk holds the full evidence: possibly split across chunks.
        "evidence_split_suspect": (not hit_at_3) and any(h["page_match"] for h in top3),
        "hits": top3,
    }


def summarize(traces: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    """Aggregate by strategy then split: counts n, hit@1, hit@3 plus 'n/N' strings."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for trace in traces:
        groups.setdefault((trace["strategy"], trace["split"]), []).append(trace)
    summary: dict[str, dict[str, dict[str, Any]]] = {}
    for (strategy, split), items in sorted(groups.items()):
        n = len(items)
        h1 = sum(1 for t in items if t["hit_at_1"])
        h3 = sum(1 for t in items if t["hit_at_3"])
        summary.setdefault(strategy, {})[split] = {
            "n": n, "hit_at_1": h1, "hit_at_3": h3,
            "hit_at_1_text": f"{h1}/{n}", "hit_at_3_text": f"{h3}/{n}"}
    return summary
