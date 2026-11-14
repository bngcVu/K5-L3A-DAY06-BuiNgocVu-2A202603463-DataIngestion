# Day 06 · Data Ingestion Engineering

**Lab cá nhân** · **4 giờ** · **Dữ liệu tự chọn**

Biến tài liệu thành kho tri thức có thể truy xuất cho AI agent, rồi đo xem cách chuẩn bị dữ liệu nào hiệu quả nhất.

| 📄 Dữ liệu | 🧩 Chiến lược | 🎯 Benchmark |
|---|---|---|
| Tối thiểu **30 trang nội dung** | Tối thiểu **3 cấu hình** | Tối thiểu **20 câu hỏi** |

## Bắt đầu nhanh

Dùng [starter PostgreSQL + pgvector](starter/README.md) để dựng database và hoàn thiện các hàm TODO, hoặc tự chọn vector store tương đương. Starter chỉ là khung: **bạn tự hiện thực pipeline và benchmark**.

## 1. Xây pipeline

![Đọc tài liệu, chuẩn hóa, chia chunk, tạo vector, lưu và kiểm tra](assets/ingestion-pipeline.svg)

Chọn tài liệu được phép sử dụng; giữ **ID tài liệu, nguồn, trang/vị trí** và metadata phù hợp. Dùng embedding model thật qua API hoặc chạy local; lưu và truy xuất bằng pgvector, Qdrant, Chroma hoặc một store tương đương.

## 2. So sánh và đánh giá

![Ba chiến lược được đánh giá trên cùng corpus và benchmark dev/test](assets/benchmark-workflow.svg)

Dùng cùng dữ liệu, model và câu hỏi cho cả ba cấu hình. Tách **15 câu dev / 5 câu test** trước khi chạy; chọn cấu hình trên dev, rồi đánh giá test sau khi cố định. Mỗi câu hỏi cần có đoạn bằng chứng và trang nguồn; thiết kế bộ câu hỏi và đánh giá theo [BENCHMARK.md](BENCHMARK.md).

Đánh giá theo năm khía cạnh: **độ chính xác truy xuất**, **tính mạch lạc của chunk**, **tính hữu dụng của metadata**, **chất lượng grounding** và **tác động của chiến lược dữ liệu**. Giải thích lựa chọn bằng kết quả, ví dụ lỗi và các đánh đổi.

## 3. Chuẩn bị bài nộp

1. Chạy pipeline và lưu cấu hình, kết quả top-3, trace cùng nhãn đánh giá thủ công.
2. Điền [thông tin học viên](STUDENT.md), [nhật ký thí nghiệm](artifacts/experiment_log.csv) và bản sao [mẫu báo cáo cá nhân](report/individual/MSSV_HoTen.md).
3. Kiểm tra mã nguồn, cách chạy, manifest dữ liệu và checklist trong [SUBMISSION.md](SUBMISSION.md).

## Tài liệu hướng dẫn

| Bạn cần tìm | Tài liệu |
|---|---|
| Setup baseline PostgreSQL + pgvector | [starter/README.md](starter/README.md) |
| Các bước làm và cách tính chỉ số | [GUIDE.md](GUIDE.md) |
| Tiến độ trong 4 giờ | [CHECKPOINTS.md](CHECKPOINTS.md) |
| Năm tiêu chí benchmark và bộ câu hỏi | [BENCHMARK.md](BENCHMARK.md) |
| Tiêu chí chấm điểm | [RUBRIC.md](RUBRIC.md) |
| Quy tắc sử dụng dữ liệu, AI và API | [RULES.md](RULES.md) |
| Cách nộp bài | [SUBMISSION.md](SUBMISSION.md) |

[data/format-example.json](data/format-example.json) chỉ minh họa định dạng, không phải corpus đủ điều kiện. Phần xử lý và benchmark do bạn tự hiện thực; không bắt buộc xây chatbot.

---

## Bài làm của tôi — Bùi Ngọc Vũ (2A202603463, L3A)

Báo cáo: [report/individual/2A202603463_BuiNgocVu.md](report/individual/2A202603463_BuiNgocVu.md) · Thông tin học viên: [STUDENT.md](STUDENT.md)

**Tóm tắt.** Corpus là *The Federalist Papers* (Project Gutenberg #1404, phạm vi công cộng): 32 bài một tác giả, 210 đơn vị nội dung, trường lọc `author`. Một model embedding local (`intfloat/multilingual-e5-small`, 384 chiều), PostgreSQL + pgvector, cosine, tìm kiếm chính xác. Ba chiến lược: **A** chunk cố định 500 ký tự (overlap 50), **B** chunk theo đoạn tối đa 1000 ký tự, **C** = B + lọc `author` khi câu hỏi có filter. Benchmark 20 câu (15 dev / 5 test). Kết quả (Hit@1 · Evidence Hit@3):

| | dev (n=15) | test (n=5) |
|---|---|---|
| A | 9/15 · 12/15 | 2/5 · 3/5 |
| B (chọn) | 11/15 · 14/15 | 4/5 · 5/5 |
| C | 11/15 · 14/15 | 4/5 · 5/5 |

Mẫu rất nhỏ nên đây chỉ là xu hướng trên corpus này, không phải kết luận tổng quát; coherence, metadata và grounding được chấm thủ công (nhãn do AI chấm, ghi rõ trong báo cáo).

**Vị trí dữ liệu và kết quả**

| Nội dung | Đường dẫn |
|---|---|
| Nguồn gốc tải về | `starter/data/raw/pg1404.txt` (từ https://www.gutenberg.org/cache/epub/1404/pg1404.txt) |
| 32 bài đã tách | `starter/data/federalist/fedNN.txt` |
| Manifest (nguồn, quyền, trang, ký tự) | `data/manifest.json` |
| Benchmark, gold, tiêu chí chọn | `benchmark/questions.json` |
| Code pipeline | `starter/src/` (đọc, chunk, embedding, lưu trữ, đánh giá, chạy), `starter/scripts/` |
| Test | `starter/tests/` |
| Kết quả, trace top-3, log, số liệu chunk | `starter/results/` |
| Nhãn chấm thủ công | `starter/results/audit/` |
| Nhật ký thí nghiệm A/B/C | `artifacts/experiment_log.csv` |

**Cách chạy** (từ thư mục `starter/`; tôi dùng Python 3.10.11 cài toàn cục, không dùng `.venv`; starter ghi 3.11–3.14 nhưng 3.10 vẫn chạy được):

```powershell
cd starter
python -m pip install -r requirements.txt
python -m pip install sentence-transformers        # tôi dùng 5.4.1 (kéo theo torch, transformers)
Copy-Item .env.example .env                        # rồi đặt EMBEDDING_MODEL=intfloat/multilingual-e5-small
docker compose -p day6_student_starter up -d --wait
python scripts/check_setup.py                      # kỳ vọng: pgvector 0.8.x và "Setup OK"

python scripts/prepare_corpus.py                   # tách 32 bài + ghi data/manifest.json
python -m src.run ingest                           # đọc, chuẩn hóa, đếm đơn vị (cần >= 30)
python -m src.run chunk                            # chunk A và B
python -m src.run index                            # embed + lưu; chạy lần 2: số dòng không đổi (1091 / 703)
python -m src.verify_benchmark                     # kiểm tra 20 câu và evidence
python -m src.run eval --split dev                 # A, B, C trên dev
python -m src.run eval --split test --final        # chỉ chạy sau khi đã chốt cấu hình
python -m unittest discover -s tests -t . -v       # 22 test (cần database đang chạy)
```

Ghi chú:
- Lần đầu chạy `index` sẽ tải model (~470 MB) từ Hugging Face; không cần API key, chi phí 0. `.env` không được commit.
- `python scripts/make_audit_sheets.py` tạo bảng chấm tay **trống** và sẽ ghi đè nhãn đã điền trong `starter/results/audit/`; không chạy lại sau khi đã chấm.
- `eval --split test` bị từ chối nếu thiếu `--final`, để tránh xem tập test trước khi chốt cấu hình.
- Thời gian embed đo trên CPU cá nhân và dao động; kết quả chạy ngày 2026-11-13.
