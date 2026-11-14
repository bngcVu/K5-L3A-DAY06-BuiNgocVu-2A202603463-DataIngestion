"""Unit tests for the pure parts of the pipeline plus a store test on a scratch table.

Run from starter/:   python -m unittest discover -s tests -t . -v

The store test uses tiny hand-made 4-dimensional vectors ONLY to check SQL behaviour
(upsert, no duplicates, filters, ordering). It says nothing about retrieval quality.
"""
import os
import unittest
from pathlib import Path

from dotenv import load_dotenv

from src.chunking import by_paragraph, fixed_size
from src.evaluation import evaluate_query, summarize
from src.ingestion import assign_sections, is_heading, normalize_text
from src.models import DocumentPage

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def page(text, number=1, document_id="doc", **meta):
    return DocumentPage(document_id, "Doc title", number, text, dict(meta))


class NormalizeTests(unittest.TestCase):
    def test_nfc_and_whitespace(self):
        decomposed = "Tiếng   Việt \ttest"
        out = normalize_text(decomposed)
        self.assertEqual(out, "Tiếng Việt test")

    def test_joins_hyphenated_and_soft_wrapped_lines(self):
        self.assertEqual(normalize_text("quy chế đào-\ntạo của trường"), "quy chế đàotạo của trường")
        self.assertEqual(normalize_text("sinh viên phải nộp\nhọc phí đúng hạn."),
                         "sinh viên phải nộp học phí đúng hạn.")

    def test_keeps_paragraphs_and_headings(self):
        text = "Chương 2 Học phí\nSinh viên nộp học phí.\n\nĐiều 5. Miễn giảm\nÁp dụng cho hộ nghèo."
        out = normalize_text(text)
        self.assertIn("\n\n", out)
        self.assertIn("Chương 2 Học phí\nSinh viên", out)
        self.assertIn("Điều 5. Miễn giảm\nÁp dụng", out)

    def test_heading_detection(self):
        for line in ["Chương 1 Tổng quan", "Điều 12. Học phí", "2.3 Kết quả", "Chapter IV"]:
            self.assertTrue(is_heading(line), line)
        for line in ["Chương trình đào tạo gồm ba năm", "2023 là năm đầu tiên.", "Sinh viên nộp hồ sơ."]:
            self.assertFalse(is_heading(line), line)

    def test_assign_sections_carries_over_pages(self):
        pages = [page("Chương 1 Tổng quan\nNội dung.", 1), page("Tiếp tục nội dung.", 2),
                 page("Chương 2 Học phí\nNội dung khác.", 3)]
        assign_sections(pages)
        self.assertEqual([p.metadata["section"] for p in pages],
                         [None, "Chương 1 Tổng quan", "Chương 1 Tổng quan"])


class FixedSizeTests(unittest.TestCase):
    def setUp(self):
        words = [f"từ{i}" for i in range(400)]
        self.page = page(" ".join(words), 7, year="2023", section="S")
        self.chunks = fixed_size(self.page, 200, 40)

    def test_size_overlap_and_no_lost_text(self):
        self.assertGreater(len(self.chunks), 5)
        for c in self.chunks:
            self.assertLessEqual(len(c.text), 200)
        joined = " ".join(c.text for c in self.chunks)
        for i in range(400):
            self.assertIn(f"từ{i}", joined)
        # consecutive chunks overlap
        self.assertTrue(set(self.chunks[0].text.split()) & set(self.chunks[1].text.split()))

    def test_no_word_is_cut(self):
        vocabulary = set(self.page.text.split())
        for c in self.chunks:
            self.assertTrue(set(c.text.split()) <= vocabulary)

    def test_metadata_and_stable_ids(self):
        first = self.chunks[0]
        self.assertEqual((first.page, first.document_id, first.source), (7, "doc", "Doc title"))
        self.assertEqual(first.metadata["year"], "2023")
        self.assertEqual(first.metadata["section"], "S")
        self.assertEqual([c.chunk_id for c in fixed_size(self.page, 200, 40)],
                         [c.chunk_id for c in self.chunks])
        self.assertEqual(len({c.chunk_id for c in self.chunks}), len(self.chunks))

    def test_invalid_params(self):
        with self.assertRaises(ValueError):
            fixed_size(self.page, 100, 100)


class ByParagraphTests(unittest.TestCase):
    TEXT = ("Điều 1. Phạm vi\n"
            + "Quy chế này áp dụng cho mọi sinh viên. " * 6 + "\n\n"
            + "Sinh viên năm cuối được ưu tiên đăng ký học phần.\n\n"
            + "Điều 2. Học phí\n"
            + "Học phí được nộp đầu mỗi học kỳ. Sinh viên nộp muộn bị phạt. " * 3)

    def test_heading_stays_with_paragraph_and_sections_are_separate(self):
        chunks = by_paragraph(page(self.TEXT), 400, 50)
        self.assertGreaterEqual(len(chunks), 2)
        for c in chunks:
            if "Học phí được nộp" in c.text:
                self.assertTrue(c.text.startswith("Điều 2. Học phí"))
                self.assertEqual(c.metadata["section"], "Điều 2. Học phí")
            if "áp dụng cho mọi sinh viên" in c.text:
                self.assertEqual(c.metadata["section"], "Điều 1. Phạm vi")
                self.assertNotIn("Điều 2", c.text)

    def test_max_size_and_sentence_split_with_overlap(self):
        long_paragraph = " ".join(f"Câu số {i} nói về quy định." for i in range(60))
        chunks = by_paragraph(page(long_paragraph), 300, 50, 1)
        self.assertGreater(len(chunks), 3)
        self.assertTrue(all(len(c.text) <= 300 for c in chunks))
        # last sentence of a chunk is repeated at the start of the next
        last_sentence = chunks[0].text.split(". ")[-1].split(" nói về")[0]
        self.assertIn(last_sentence, chunks[1].text)

    def test_small_trailing_chunk_is_merged(self):
        chunks = by_paragraph(page("Đoạn một khá dài " * 20 + "\n\nĐoạn ngắn."), 400, 100)
        self.assertTrue(all(len(c.text) >= 100 for c in chunks))

    def test_short_leading_piece_merges_forward_and_tiny_tail_is_kept(self):
        text = ("Tiêu đề bài viết\n\nKính gửi các bạn:\n\n" + "Nội dung chính của bài viết. " * 25
                + "\n\nPUBLIUS")
        chunks = by_paragraph(page(text), 800, 200)
        self.assertTrue(chunks[0].text.startswith("Tiêu đề bài viết"))
        self.assertIn("Nội dung chính", chunks[0].text)
        self.assertEqual(chunks[-1].text.splitlines()[-1], "PUBLIUS")  # not lost, not alone
        self.assertTrue(all(len(c.text) <= 800 + 100 for c in chunks))
        joined = "\n".join(c.text for c in chunks)
        for word in ("Tiêu đề bài viết", "Kính gửi các bạn:", "PUBLIUS"):
            self.assertIn(word, joined)

    def test_one_page_per_chunk_and_stable_ids(self):
        p = page(self.TEXT, 9)
        a, b = by_paragraph(p, 400, 50), by_paragraph(p, 400, 50)
        self.assertEqual([c.chunk_id for c in a], [c.chunk_id for c in b])
        self.assertTrue(all(c.page == 9 for c in a))

    def test_empty_page_gives_no_chunks(self):
        self.assertEqual(by_paragraph(page(""), 400), [])
        self.assertEqual(fixed_size(page(""), 200, 20), [])


class EvaluationTests(unittest.TestCase):
    QUESTION = {"id": "q1", "split": "dev", "question": "?",
                "gold": [{"source_id": "doc", "page": 3}, {"source_id": "doc", "page": 8}],
                "evidence_quote": "học phí nộp trước ngày 15"}

    @staticmethod
    def hit(rank, page_no, text, document_id="doc"):
        return {"rank": rank, "chunk_id": f"c{rank}", "score": 1 - rank / 10, "document_id": document_id,
                "source": "s", "page": page_no, "metadata": {}, "text": text}

    def test_hit_at_1_and_3(self):
        hits = [self.hit(1, 5, "không liên quan"), self.hit(2, 8, "Học  phí NỘP trước ngày 15 hàng tháng"),
                self.hit(3, 1, "x")]
        result = evaluate_query(self.QUESTION, hits)
        self.assertFalse(result["hit_at_1"])
        self.assertTrue(result["hit_at_3"])
        self.assertEqual(result["first_hit_rank"], 2)  # alternative gold page 8 is accepted

    def test_right_page_but_missing_evidence_is_not_a_hit(self):
        result = evaluate_query(self.QUESTION, [self.hit(1, 3, "chỉ nói về học phí chung chung")])
        self.assertFalse(result["hit_at_3"])
        self.assertTrue(result["evidence_split_suspect"])

    def test_wrong_document_is_not_a_hit(self):
        result = evaluate_query(self.QUESTION, [self.hit(1, 3, "học phí nộp trước ngày 15", "other")])
        self.assertFalse(result["hit_at_1"])

    def test_summarize_counts(self):
        def trace(strategy, split, h1, h3):
            return {"strategy": strategy, "split": split, "hit_at_1": h1, "hit_at_3": h3}
        summary = summarize([trace("A", "dev", True, True), trace("A", "dev", False, True),
                             trace("A", "dev", False, False), trace("B", "test", True, True)])
        self.assertEqual(summary["A"]["dev"]["hit_at_1_text"], "1/3")
        self.assertEqual(summary["A"]["dev"]["hit_at_3_text"], "2/3")
        self.assertEqual(summary["B"]["test"]["hit_at_3_text"], "1/1")


@unittest.skipUnless(os.getenv("DATABASE_URL"), "DATABASE_URL not set")
class StoreTests(unittest.TestCase):
    """Needs the Docker database from starter/README.md. Uses scratch table chunks_test."""

    def setUp(self):
        from src.store import PgVectorStore
        self.store = PgVectorStore(os.environ["DATABASE_URL"], 4, table="chunks_test")
        self.store.drop_table()
        self.store.create_schema()
        self.chunks = []
        for i, (year, vector) in enumerate([("2022", [1, 0, 0, 0]), ("2023", [0.9, 0.1, 0, 0]),
                                            ("2023", [0, 1, 0, 0]), ("2022", [0, 0, 1, 0])]):
            c = fixed_size(page(f"nội dung số {i} " * 5, i + 1, year=year), 500, 0)[0]
            self.chunks.append(c)
        self.vectors = [[1, 0, 0, 0], [0.9, 0.1, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]]

    def tearDown(self):
        self.store.drop_table()

    def test_upsert_is_idempotent_and_removes_obsolete_chunks(self):
        self.store.upsert("A", self.chunks, self.vectors)
        self.store.upsert("A", self.chunks, self.vectors)
        self.assertEqual(self.store.count("A"), 4)
        self.store.upsert("A", self.chunks[:2], self.vectors[:2])  # document changed: 2 chunks left
        self.assertEqual(self.store.count("A"), 2)

    def test_search_orders_by_cosine_and_filters_before_ranking(self):
        self.store.upsert("A", self.chunks, self.vectors)
        hits = self.store.search("A", [1, 0, 0, 0], 3)
        self.assertEqual([h["rank"] for h in hits], [1, 2, 3])
        self.assertEqual(hits[0]["page"], 1)
        self.assertGreaterEqual(hits[0]["score"], hits[1]["score"])
        filtered = self.store.search("A", [1, 0, 0, 0], 3, {"year": "2023"})
        self.assertEqual({h["metadata"]["year"] for h in filtered}, {"2023"})
        self.assertEqual(len(filtered), 2)
        self.assertEqual(self.store.search("B", [1, 0, 0, 0], 3), [])

    def test_validation(self):
        with self.assertRaises(ValueError):
            self.store.upsert("A", self.chunks, self.vectors[:-1])
        with self.assertRaises(ValueError):
            self.store.upsert("A", self.chunks[:1], [[1, 0, 0]])
        with self.assertRaises(ValueError):
            self.store.search("A", [1, 0, 0, 0], 3, {"text; DROP TABLE chunks": "x"})
        with self.assertRaises(ValueError):
            self.store.search("A", [1, 0, 0], 3)


if __name__ == "__main__":
    unittest.main()
