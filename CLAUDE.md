# CLAUDE.md – MWG Financial Data Lakehouse

Hướng dẫn dành cho Claude Code khi làm việc trong repo này. Đọc toàn bộ file trước khi sửa code.

## 1. Mục tiêu dự án

Đồ án xây dựng **Data Lakehouse cho Báo cáo tài chính (BCTC) của CTCP Đầu tư Thế Giới Di Động (mã HOSE: MWG)**.

- **Nguồn dữ liệu chính:** tài liệu do chính MWG công bố trên trang Quan hệ nhà đầu tư `https://mwg.vn` (BCTC hợp nhất quý/bán niên/năm dạng PDF, báo cáo KQKD lũy kế tháng, báo cáo thường niên).
- **Luồng:** crawl tự động → Bronze → Silver → Gold → Dashboard.
- **Ràng buộc cứng: chi phí 0 đồng.** Chỉ dùng thư viện mã nguồn mở chạy local trên laptop; dịch vụ cloud chỉ được dùng bản miễn phí (GitHub Actions cho repo public, Streamlit Community Cloud).
- Dữ liệu nhỏ (dưới ~50.000 dòng). Giá trị của đồ án nằm ở pipeline end-to-end từ tài liệu gốc, chất lượng dữ liệu và kiến trúc Lakehouse mở rộng được, không nằm ở "Big Data".

## 2. Tech stack (không tự ý thay đổi)

| Thành phần | Công cụ |
|---|---|
| Crawl | `requests`, `beautifulsoup4` + `lxml`, `tenacity`, `requests-cache`; `playwright` chỉ khi danh sách tài liệu render bằng JavaScript |
| Bóc tách PDF | `pdfplumber` (chính); `camelot` lattice, PaddleOCR / Tesseract `vie` là dự phòng |
| Định dạng Lakehouse | Delta Lake qua `deltalake` (delta-rs) |
| Xử lý | `polars` (chính), `pandas` khi thư viện trả về Pandas |
| Truy vấn | `duckdb` + extension `delta` (`delta_scan`) |
| Chất lượng dữ liệu | `pandera` + kiểm tra nghiệp vụ tự viết |
| BI | `streamlit` + `plotly` |
| Điều phối | `run_pipeline.py` + GitHub Actions cron (hoặc Windows Task Scheduler) |
| Đối soát | `vnstock` (KBS/VCI) |

Quy tắc:
- **Không dùng Spark/PySpark trong pipeline chính.** Nếu có phần Spark thì chỉ nằm trong file riêng (vd. `src/transform/silver_to_gold_spark.py`) phục vụ benchmark, đọc/ghi cùng bảng Delta.
- **Không thêm dịch vụ hoặc thư viện trả phí**, proxy trả phí, dịch vụ giải CAPTCHA.
- Mọi dependency mới phải thêm vào `requirements.txt`; pin version sau khi chạy thử thành công.
- Python 3.11. Dùng `venv` hoặc `uv`.

## 3. Cấu trúc thư mục

```
mwg-lakehouse/
├── config/
│   ├── sources.yaml          # URL trang danh sách, CSS selector, quy tắc nhận diện tài liệu
│   ├── account_mapping.csv   # tên chỉ tiêu (đã chuẩn hóa) -> mã số TT200
│   ├── dim_account.csv       # mã số, tên VI/EN, báo cáo, cấp, mã cha, is_subtotal, sign
│   └── dim_chain.csv         # MWG_TOTAL, TGDD, DMX, BHX, AKA, ERABLUE, ...
├── data/
│   ├── _state/               # manifest.parquet, unmapped_items.csv
│   ├── bronze/source=mwg_ir/doc_type=<TYPE>/ingestion_date=YYYY-MM-DD/
│   ├── bronze/source=vnstock_kbs/...   # chỉ dùng để đối soát
│   ├── silver/               # fs_extracted_raw/, fin_statement_long/, chain_kpi/ (Delta)
│   └── gold/                 # mwg.duckdb, dim_*/, fact_financial/ (Delta, partition fiscal_year)
├── src/
│   ├── common/               # text.py (norm), paths.py
│   ├── ingestion/            # http_utils.py, mwg_crawler.py, vnstock_check.py
│   ├── extraction/           # pdf_extractor.py
│   ├── transform/            # bronze_to_silver.py, silver_to_gold.py
│   └── quality/              # checks.py
├── dashboard/app.py
├── tests/
├── .github/workflows/crawl.yml
├── run_pipeline.py
└── requirements.txt
```

Phân vùng thư mục theo kiểu Hive `key=value` để DuckDB/Polars đọc phân vùng như cột.

## 4. Lệnh thường dùng

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
# Máy không có pip/python3-venv: uv venv --python 3.11 .venv && uv pip install -r requirements.txt

python run_pipeline.py --backfill    # chỉ lần đầu: crawl toàn bộ lịch sử (chậm, có nghỉ)
python run_pipeline.py               # incremental: chỉ tài liệu mới
python run_pipeline.py --force       # chạy lại Silver/Gold dù không có tài liệu mới

python -m src.ingestion.mwg_crawler          # chỉ crawl
python -m src.transform.bronze_to_silver     # chỉ Bronze -> Silver
python -m src.transform.silver_to_gold       # chỉ Silver -> Gold

streamlit run dashboard/app.py
pytest -q
```

Khi phát triển crawler, bật `requests-cache` để không gọi lại mwg.vn khi debug.

## 5. Quy tắc theo từng tầng

### Ingestion (crawl mwg.vn)
- **Không hard-code** URL trang danh sách, CSS selector, từ khóa nhận diện tài liệu. Tất cả nằm trong `config/sources.yaml`. Nếu selector chưa được điền (còn `<...>`), dừng lại và báo người dùng khảo sát trang bằng DevTools, đừng đoán.
- Nếu trong DevTools có endpoint JSON trả danh sách tài liệu, ưu tiên gọi endpoint đó thay vì parse HTML.
  Đã khảo sát (11/11/2026): `mwg.vn/bao-cao` lấy danh sách từ JSON API `api.bhxwork.vn/gw/ir-api`
  (`CategoryAPI` → danh mục theo năm, `PostAPI` → tài liệu). Cấu hình nằm trong `sources.yaml > listing`.
- Mọi request đi qua `src/ingestion/http_utils.get()`:
  - Session dùng chung, header trình duyệt thật (User-Agent xoay vòng, `Accept-Language: vi-VN`, `Referer`).
  - Nghỉ ngẫu nhiên 2–5 giây giữa request, 5–10 giây khi tải PDF.
  - Retry exponential backoff (tối đa 4 lần) cho 429/5xx/timeout.
  - **Gặp 403 thì dừng và ghi log, không tìm cách vượt qua.** Không giải CAPTCHA, không dùng proxy.
- **Incremental bằng manifest** (`data/_state/manifest.parquet`, khóa là URL): API trả nguyên một năm mỗi request, nên incremental chỉ xem `incremental_years` (= 2) năm mới nhất; `--backfill` xem tất cả. Dùng `--dry-run` để xem trước tài liệu sẽ tải.
- Tài liệu cùng kỳ nhưng sha256 khác (bản điều chỉnh, bản kiểm toán thay bản tự lập) vẫn được lưu; tầng Silver quyết định bản nào thắng.
- Tôn trọng `robots.txt` và Điều khoản sử dụng mwg.vn (chỉ dùng cho mục đích cá nhân, phi thương mại, học thuật).

### Bronze
- **Append-only.** Không sửa, không xóa file đã tải.
- Mỗi file PDF có `<tên>.meta.json`: `url, title, doc_type, period, published, sha256, size, fetched_at, http_status`.
- Tên file: `MWG_{kỳ}_{sha256[:8]}.pdf` (kỳ dạng `2026Q3`, `2026H1`, `2025`).
- Loại tài liệu: `FS_CONSOLIDATED_Q`, `FS_CONSOLIDATED_H`, `FS_CONSOLIDATED_Y`, `MONTHLY_RESULT` (có thể thêm loại khác qua `doc_rules`).

### Bóc tách PDF
- Lưu ý: BCTC hợp nhất Q3/2026 là PDF scan (51 trang ảnh, không có lớp text), nên OCR có thể là đường chính chứ không chỉ dự phòng. Kiểm tra lớp text của từng file trước khi chọn cách bóc.
- Neo theo cột **"Mã số"** của Thông tư 200 (B01: 100, 110, 270, 300, 400...; B02: 01, 10, 20, 60...), không neo theo tên chỉ tiêu.
- Xác định trang theo tiêu đề báo cáo (tiếng Việt và tiếng Anh), so sánh trên chuỗi đã bỏ dấu, chữ thường.
- Số tiền kiểu Việt Nam: `1.234.567` là số dương, `(1.234)` là số âm, `-` hoặc rỗng là null. Đọc dòng "Đơn vị tính" để quy đổi về VND.
- Tên chỉ tiêu bị xuống dòng phải được nối lại.
- Ngay sau khi bóc, kiểm tra: `270 = 300 + 400` (B01), `10 = 01 − 02` và `20 = 10 − 11` (B02). Sai thì gắn cờ `dq_balance_ok = false`, **không** đẩy kỳ đó sang Gold.

### Silver
- Dạng dài: `symbol, statement (B01/B02/B03), fiscal_year, fiscal_quarter, period_kind, account_code, item_name, amount_vnd, source_priority, source_doc_sha256, dq_balance_ok`.
- Kỳ: `2026-Q3` → `fiscal_year=2026, fiscal_quarter=3`; báo cáo năm dùng `fiscal_quarter=0`. B02 quý giữ cả "quý này" và "lũy kế".
- Mã tài khoản: ưu tiên mã số in trên báo cáo; nếu thiếu thì ánh xạ qua `config/account_mapping.csv`. Dòng không ánh xạ được ghi ra `data/_state/unmapped_items.csv`; **không tự bịa mã**.
- Ghi bằng **Delta MERGE** theo khóa `(symbol, statement, fiscal_year, fiscal_quarter, period_kind, account_code)`. Độ ưu tiên nguồn: kiểm toán (3) > soát xét (2) > tự lập (1). Không bao giờ để bản ưu tiên thấp ghi đè bản ưu tiên cao.
- Đối soát 10–15 chỉ tiêu chính với `vnstock`; chênh > 0,1% thì ghi vào báo cáo DQ. **Dữ liệu vnstock không bao giờ được ghi vào Gold.**

### Gold (Star Schema)
- `fact_financial`: grain = 1 chỉ tiêu × 1 kỳ × 1 phạm vi. Số hợp nhất dùng `chain_key = 'MWG_TOTAL'`, `scope = 'consolidated'`; số theo chuỗi dùng `scope = 'segment'`. **Không bao giờ cộng lẫn hai scope.**
- `dim_date`, `dim_account`, `dim_chain` ghi đè mỗi lần chạy. `fact_financial` ghi Delta, partition `fiscal_year`.
- View KPI trong DuckDB (`v_kpi_quarter`, ...): biên LN gộp, biên LN ròng, ROE, ROA, số ngày tồn kho, YoY/QoQ, tỷ trọng doanh thu theo chuỗi. Luôn dùng `NULLIF(mẫu, 0)` khi chia.

### Dashboard
- Mở DuckDB ở chế độ `read_only=True`. Không chạy logic transform trong dashboard.
- Hiển thị tiền theo tỷ đồng; dữ liệu lưu theo VND.

## 6. Quy ước code

- Đọc/ghi đường dẫn qua `pathlib.Path`, mở file với `encoding="utf-8"`.
- Mọi lời gọi `vnstock` nằm trong **một** adapter (`src/ingestion/vnstock_check.py`), vì API của thư viện này thay đổi giữa các phiên bản.
- Hàm chuẩn hóa chuỗi tiếng Việt (bỏ dấu, `đ`→`d`, chữ thường, gộp khoảng trắng) dùng chung một hàm `norm()`; không viết lại.
- Log bằng `print` có tiền tố tầng (`[crawl]`, `[bronze]`, `[DQ]`, `[gold]`) hoặc `logging` với cùng tiền tố.
- Không commit thông tin cá nhân hay token. GitHub Actions chỉ dùng `GITHUB_TOKEN` mặc định.
- Comment và thông điệp log có thể viết tiếng Việt; tên biến, hàm, cột dùng tiếng Anh dạng `snake_case`.

## 7. Kiểm thử và Definition of Done

Trước khi coi một thay đổi là xong:
- `pytest -q` pass. Test tối thiểu: `to_number` (số âm trong ngoặc, dấu chấm phân cách), `norm`, `classify`, `parse_period`, kiểm tra phương trình kế toán trên một PDF mẫu trong `tests/fixtures/`.
- Chạy `run_pipeline.py` lần thứ hai liên tiếp phải in "Không có tài liệu mới" hoặc chỉ tải 0–2 file.
- `unmapped_items.csv` rỗng (hoặc đã giải thích từng dòng).
- KPI của ít nhất 2 kỳ khớp số tự tính trên PDF gốc.

## 8. Lộ trình (10 tuần) – để biết đang ở giai đoạn nào

| Tuần | Giai đoạn | Kết quả cần có |
|---|---|---|
| 1 | Khởi động & khảo sát mwg.vn | `sources.yaml` đầy đủ, ảnh chụp điều khoản sử dụng |
| 2–3 | Crawler + Bronze | Incremental chạy đúng, manifest khớp số file |
| 4–5 | Bóc tách PDF | ≥ 95% kỳ qua kiểm tra phương trình kế toán |
| 6 | Silver | Delta MERGE, đối soát vnstock, `chain_kpi` |
| 7 | Gold | Star schema + view KPI khớp Excel |
| 8 | Dashboard | Streamlit chạy local và trên Community Cloud |
| 9 | Tự động hóa | GitHub Actions cron, demo Delta time travel |
| 10 | Hoàn thiện | Báo cáo, slide, video demo |

Ưu tiên khi thiếu thời gian: B01 + B02 hợp nhất → Gold → dashboard trước; B03, báo cáo tháng và số liệu theo chuỗi là phần mở rộng.

## 9. Những điều Claude không được làm

- Không xóa hoặc sửa file trong `data/bronze/`.
- Không đổi stack (thêm Spark vào pipeline chính, thay Delta bằng định dạng khác, thêm dịch vụ trả phí) khi chưa hỏi người dùng.
- Không đoán selector hay URL của mwg.vn; không viết code né cơ chế chống bot.
- Không tự điền số liệu tài chính "từ trí nhớ" vào test, fixture hay mapping; số liệu phải lấy từ PDF gốc.
- Không ghi dữ liệu từ nguồn đối soát (vnstock, CafeF) vào tầng Gold.
