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
│   ├── gold/                 # mwg.duckdb, dim_*/, fact_financial/ (Delta, partition fiscal_year)
│   └── _cache/ocr/           # cache OCR theo sha256 + trang (không commit)
├── src/
│   ├── common/               # text.py (norm), paths.py
│   ├── ingestion/            # http_utils.py, mwg_crawler.py, vnstock_check.py
│   ├── extraction/           # ocr.py, pdf_extractor.py, run_extraction.py
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
python run_pipeline.py --force       # chạy lại bóc tách/Silver/Gold từ Bronze sẵn có, không crawl (không gọi mwg.vn)
python run_pipeline.py --force --crawl   # crawl rồi chạy lại toàn bộ

python -m src.ingestion.mwg_crawler          # chỉ crawl
python -m src.extraction.run_extraction      # bóc B01/B02/B03 (OCR) + DQ -> silver/fs_extracted_raw, docs/dq_report.md
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
- **Hai ngôn ngữ, cùng do MWG công bố:** `vi-VN` (danh mục gốc 7) là nguồn chính, luôn tải. `en-US` (danh mục gốc 61, nhóm BCTC và nhóm báo cáo tháng "YTD … report") là nguồn dự phòng: một tài liệu en-US chỉ được tải khi kỳ `(doc_type, period)` chưa có bản vi-VN tải được (link vi-VN chết hoặc không đăng), hoặc khi kỳ đó nằm trong `listing.cross_check` (hiện là Q3/2020 và FY2020, để đối chiếu hai bản trong `docs/dq_report.md`). Manifest ghi `lang` và `fetch_reason` (`primary` / `fallback` / `cross_check`).
- Crawler gom **toàn bộ listing trước** (mọi ngôn ngữ), rồi nhóm theo URL. URL được nguồn gắn cho nhiều kỳ khác nhau (nguồn trỏ nhầm link): mọi mục của URL đó có `period_ambiguous = true`, file tải **một lần** với `period = pending` (tên file `MWG_pending_…`, `doc_type = PENDING` nếu các kỳ ứng viên khác loại tài liệu), `period_candidates` liệt kê các cặp `doc_type:kỳ`, và mỗi mục ghi một dòng vào `data/_state/link_anomalies.csv` (`title, period_from_title, url, period_in_manifest, detected_at`) + `docs/dq_report.md`. File đã lưu trước đó với một kỳ cụ thể thì manifest được chuyển sang `pending` (không sửa Bronze). Kỳ thật được gán sau bước đối chiếu ngày báo cáo trong PDF bằng `mwg_crawler.resolve_period()`.
- Link chết (404/410) ghi vào `data/_state/download_errors.csv` rồi đi tiếp; lần backfill sau vẫn thử lại. **Không dùng Wayback Machine** hay bản lưu của bên thứ ba; kỳ không lấy lại được ghi vào `docs/limitations.md`.
- Mọi request đi qua `src/ingestion/http_utils.get()`:
  - Session dùng chung, header trình duyệt thật (User-Agent xoay vòng, `Accept-Language: vi-VN`, `Referer`).
  - Nghỉ ngẫu nhiên 2–5 giây giữa request, 5–10 giây khi tải PDF.
  - Retry exponential backoff (tối đa 4 lần) cho 429/5xx/timeout.
  - **Gặp 403 thì dừng và ghi log, không tìm cách vượt qua.** Không giải CAPTCHA, không dùng proxy.
- Mọi entrypoint ghi dữ liệu/config (`run_pipeline.py`, `python -m src...`, `tools/...`) giữ khóa chung `data/_state/pipeline.lock` (`src/common/lock.py`, PID + thời điểm): không chạy hai lượt cùng lúc; `--force` không crawl, muốn crawl kèm thì thêm `--crawl`.
- **Incremental bằng manifest** (`data/_state/manifest.parquet`, khóa là URL): API trả nguyên một năm mỗi request, nên incremental chỉ xem `incremental_years` (= 2) năm mới nhất; `--backfill` xem tất cả. Dùng `--dry-run` để xem trước tài liệu sẽ tải.
- Tài liệu cùng kỳ nhưng sha256 khác (bản điều chỉnh, bản kiểm toán thay bản tự lập, bản ngôn ngữ khác) vẫn được lưu; tầng Silver quyết định bản nào thắng. `is_restatement` chỉ so trong cùng một ngôn ngữ.
- Tôn trọng `robots.txt` và Điều khoản sử dụng mwg.vn (chỉ dùng cho mục đích cá nhân, phi thương mại, học thuật).

### Bronze
- **Append-only.** Không sửa, không xóa file đã tải.
- Mỗi file có `<tên>.meta.json`: `url, lang, fetch_reason, title, doc_type, file_type, has_text_layer, period, period_ambiguous, period_candidates, published, assurance_level, sha256, size, fetched_at, http_status`. File tải trước khi có các trường mới thì thiếu chúng; manifest coi là `lang=vi-VN`, `fetch_reason=primary`, `file_type=pdf`, `has_text_layer=null`.
- `has_text_layer`: PDF có lớp text đọc được bằng `pdfplumber` (True) hay là bản scan cần OCR (False); ảnh luôn là False; null = không mở được file.
- `assurance_level` (dùng làm `source_priority` ở Silver) theo ngôn ngữ: vi-VN dùng từ khóa "kiểm toán"/"soát xét"; en-US FY = 3, 1H = 2, còn "Audited" trên BCTC quý chỉ tính 2 vì bản vi-VN cùng kỳ ghi "Soát Xét".
- Tên file: `MWG_{kỳ}_{sha256[:8]}.{pdf|jpg|png}` (kỳ dạng `2026Q3`, `2026H1`, `2025`, `2017M08`, hoặc `pending`). Với file `pending`, kỳ đúng nằm trong manifest, không nằm trong tên file/`meta.json`. `file_type` lấy theo magic bytes của nội dung, không theo đuôi URL. Ảnh (báo cáo tháng 2017–01/2018) cần OCR ở bước bóc tách.
- Loại tài liệu: `FS_CONSOLIDATED_Q`, `FS_CONSOLIDATED_H`, `FS_CONSOLIDATED_Y`, `MONTHLY_RESULT` (có thể thêm loại khác qua `doc_rules`).

### Bóc tách PDF
- **Chọn đường bóc theo `has_text_layer` trong manifest:** `true` → `pdfplumber` (lấy từ + tọa độ); `false` (PDF scan, ảnh `.jpg`/`.png`) → **OCR là đường chính** bằng Tesseract (`vie+eng`) qua `pytesseract`, render trang bằng `pypdfium2`. Cả hai đường cho ra cùng một dạng "từ + bbox" để dùng chung bộ dựng bảng.
- **OCR hai lượt:** Pass 1: xoay trang theo OSD của Tesseract (150 dpi; trang B02 quý in ngang, mỗi trang một chiều), OCR text 100 dpi, tìm trang B01/B02/B03 theo mã mẫu (`B01-DN/HN`, `B01a~DN/HN`) hoặc tiêu đề bằng `norm()`, dừng khi gặp thuyết minh. Pass 2 chỉ những trang đó ở 300 dpi: Tesseract TSV `vie+eng` cả trang để lấy bố cục và tên chỉ tiêu; rồi **OCR riêng từng cột số tiền** (`--psm 6`, whitelist `0123456789.,()-`) và **cột mã số** (whitelist `0123456789ab`), đã xóa đường kẻ bảng, thay cho số/mã OCR cả trang. Gom từ thành dòng theo trục y, cột theo mép phải, neo cột "Mã số"/"Code"; kỳ của từng cột đọc từ tiêu đề cột (kể cả tiêu đề nhóm "Quý III", "Lũy kế…" nằm trên hai cột).
- **Cache OCR** theo `sha256 + trang + dpi` trong `data/_cache/ocr/` (không commit). Chạy lại không OCR lại.
- Neo theo cột **"Mã số"** in trên báo cáo, không neo theo tên chỉ tiêu.
- Xác định trang theo tiêu đề báo cáo (tiếng Việt và tiếng Anh, **cả hai tên B01**: "Bảng cân đối kế toán" và "Báo cáo tình hình tài chính"), so sánh trên chuỗi đã bỏ dấu, chữ thường.
- Không nhận diện được regime: năm < 2026 thì coi là `TT200` và gắn cờ `regime_inferred`.
- **Cột B02 theo TT99** (thứ tự cột khác TT200, **không gán theo thứ tự cột**): đọc tiêu đề cột; không đọc được thì OCR lại dải tiêu đề ở 500 dpi; vẫn không được thì chứng minh nhãn bằng số: hai cột năm trước khớp báo cáo cùng kỳ năm trước (≥ 3 mã chuẩn), hai cột năm nay phân biệt bằng lũy kế − quý này = lũy kế kỳ trước cùng năm (Q3: bán niên, Q2: Q1; Q1: hai cột bằng nhau). Ghi cách gắn nhãn ở `b02_label_method` (`header`, `header_reocr`, `numeric`, `positional`). Không chứng minh được thì `columns_ok = false`, không đưa sang Silver. Kiểm tra phụ: báo cáo Q1 có cột "quý này" = cột "lũy kế".
- **`value_origin`** của mỗi ô: `ocr`, `ocr_reocr` (OCR lại ô/dải mã), `paddle`, `consensus_fixed`, `consensus_filled`, `manual`. `docs/dq_report.md` báo riêng độ chính xác OCR thô (trước mọi bước tự sửa) và tỷ lệ cân đối sau tự sửa.
- **Chế độ kế toán (regime)** nhận diện theo tên B01 in trên báo cáo: "Bảng cân đối kế toán"/"Balance sheet" → `TT200` (TT200 + TT202 cho hợp nhất); "Báo cáo tình hình tài chính"/"Statement of financial position" → `TT99` (TT99/2025/TT-BTC, MWG dùng từ BCTC Q3/2026; nhiều mã B01/B02 dịch chuyển, vd. tổng tài sản 270 → 280). Mã chuẩn = mã TT200; mã TT99 quy đổi qua `config/account_code_regime_map.csv`. `config/dim_account.csv` (có cột `regime, is_additive, sign, parent_code`) và bảng map **chỉ dựng từ văn bản gốc trong `docs/reference/`** bằng `tools/build_dim_account.py`; mã không chắc để trống, danh sách và nguồn trong `docs/dim_account_notes.md`. Mã bộ đọc bỏ sót (vd. TT99 410) do người dùng tự đọc trên văn bản gốc và ghi vào `config/dim_account_overrides.csv` (`regime, statement, code, name, parent_code, sign, source_file, page, verified_by`); builder chỉ áp dụng dòng có `source_file` và `verified_by`. Bảng map ghép theo tên chỉ tiêu trùng khớp, không tự ghép chỉ tiêu đổi tên; sau đó `tools/map_tt99_numeric.py --ref 2025` ghép thêm bằng số 31/12/2025 (đầu năm BCTC 2026 = cuối năm BCTC 2025 đã kiểm toán, khớp duy nhất → `mapping_basis = numeric_31122025`). Mã ghép theo tên mà mọi báo cáo 2026 lệch số TT200 (BCTC năm 2025; thiếu ô thì dùng BCTC Q4/2025 cùng ngày), không giống lỗi OCR → `semantic_break = true` (vd. 120, 130, 135: TT99 phân loại lại); mã đã ghép biến động > 20% giữa hai ngày được liệt kê trong `docs/dim_account_notes.md` để người dùng duyệt: không so chéo cột đầu năm 2026 với cuối năm 2025, KPI ở Gold không nối chuỗi qua mã này.
- Số tiền đọc dễ tính với dấu phân cách OCR sai (`90.899.467872` → giữ chữ số); có chữ cái thì là lỗi. Sai chữ số sẽ lộ ở kiểm tra DQ.
- Số tiền kiểu Việt Nam: `1.234.567` là số dương, `(1.234)` là số âm, `-` hoặc rỗng là null. Đọc dòng "Đơn vị tính" để quy đổi về VND.
- Tên chỉ tiêu bị xuống dòng phải được nối lại.
- Ngay sau khi bóc, kiểm tra (sai số cho phép do làm tròn: 1 đơn vị tính in trên báo cáo):
  - phương trình **theo regime** trong `config/dq_equations.csv` (không viết cứng mã trong code): TT200 `270 = 300 + 400`, `10 = 01 − 02`, `20 = 10 − 11`; TT99 dùng các công thức in trên báo cáo (`280 = 100 + 200`, `440 = 300 + 400`, `30 = 20 + 22 − (23 + 25 + 26) + 27`, ...); `60 = 61 + 62` cả hai regime; TT200 mã 30 dùng công thức dò bằng số liệu (`tools/probe_b02_formula.py`, đúng > 90% báo cáo qua critical B01) cho tới khi có TT202 chính thức. Cột `optional` = mã vế phải được phép không in (báo cáo bỏ dòng không có số liệu, vd. 24) → tính là 0;
  - **đồng nhất thức** trong `config/dq_identities.csv` (TT200 `440 := 270`, TT99 `440 := 280`): ô đích lấy giá trị ô nguồn, `value_origin = identity`, giữ số OCR ở `ocr_value`, bỏ phép kiểm tổng dòng con của ô đích; ô đích trusted khi ô nguồn trusted;
  - **trị tuyệt đối chỉ cho mã có `sign = -1` trong `dim_account`** (khoản giảm trừ: báo cáo TT200 của MWG in âm trong ngoặc, báo cáo TT99 in dương); mã khác (30/40/50/60...) giữ đúng dấu in;
  - số đầu năm N (B01) = số cuối năm N−1 khi có cả hai tài liệu (so theo mã chuẩn qua bảng map, kể cả kỳ TT99);
  - tổng các dòng con = dòng tổng theo `parent_code` trong `config/dim_account.csv` (khi đã có danh mục).
  Sai thì gắn cờ `dq_balance_ok = false`. Chặn Gold theo **mức độ và theo từng báo cáo**: critical B01 (phương trình cân đối B01, kỳ/ngày, có trang B01) chặn ô B01; critical B02 (phương trình B02 vế trái 10/20/30/50/60, kỳ/ngày, có trang B02, nhãn cột TT99) chặn ô B02; warning (tổng dòng con mục nhỏ, đầu năm ≠ cuối năm trước, Q1 quý này = lũy kế) chỉ ghi lại. Ô **trusted** = nằm trong một phương trình đúng, ≥ 2 nguồn bỏ phiếu khớp, hoặc đồng nhất thức của ô trusted. Gold chỉ nhận ô `gold_eligible` (trusted và critical của chính báo cáo đó qua); B03 chưa có critical nên chưa vào Gold. Kết quả ghi vào `docs/dq_report.md` theo kỳ.
  Số cuối năm N lệch đúng một chữ số thừa/thiếu so với số đầu năm (khớp nhau) của mọi báo cáo N+1 → gợi ý pending (`suggested_by = adjacent_period`), không coi là trình bày lại.
- **Đối chiếu ngày báo cáo** (vi + en, sau OCR nếu là bản scan): đọc ngày kết thúc kỳ in trong báo cáo (vi: "tại ngày 30 tháng 9 năm 2017", "cho kỳ … kết thúc ngày …"; en: "as at 30 September 2017", "for the period ended …"; báo cáo tháng: tháng lũy kế) và so với `period` trong manifest (Q1 → 31/03, Q2/H1 → 30/06, Q3 → 30/09, Q4/năm → 31/12). Lệch hoặc không đọc được thì gắn cờ `period_check_ok = false`, ghi vào `docs/dq_report.md` và **không đưa tài liệu đó vào Silver**. Ngày đọc được phải **trước `publishDate` và không quá ~4 tháng** (125 ngày); vi phạm (vd. OCR đọc 2023 thành 2025) thì không tin ngày đó: giữ kỳ theo tiêu đề và gắn cờ `ocr_date_suspect`. File `period = pending` được gán kỳ thật theo ngày đọc được (`resolve_period`); không đọc được thì giữ `pending`.
- **Tự sửa lỗi OCR** (theo thứ tự, chỉ khi khác biệt trông giống lỗi OCR: cùng số chữ số và sai ≤ 2 chữ số, hoặc chỉ mất dấu âm, để không ghi đè số trình bày lại):
  1. dòng có số tiền mà thiếu mã → OCR lại dải mã đó (400 dpi, làm mảnh nét, `--psm 7`); ô số sai mẫu nhóm 3 chữ số → OCR lại riêng ô đó;
  2. bỏ phiếu đa số giữa các lần một số B01 được in lại (cuối kỳ BCTC năm/Q4 N = đầu năm của các báo cáo N+1), so theo mã chuẩn: ≥ 2 nguồn khớp và hơn hẳn → `is_consensus`, ô lệch được sửa (`corrected_by = consensus`, giữ `ocr_value`); ô thiếu chỉ được điền khi mọi nguồn khớp nhau;
  3. báo cáo còn sai phương trình đánh giá được → PaddleOCR (mô hình mobile, CPU) riêng các trang đó, giữ nếu ít lỗi hơn;
  4. phương trình sai mà chỉ còn đúng một ô có thể sửa (sau khi loại các ô đã xác nhận bằng bỏ phiếu hoặc phương trình khác đúng) → ghi giá trị gợi ý vào `manual_overrides.csv` với `suggested_by = equation`, `status = pending`; **không áp dụng** cho tới khi người dùng đổi `status` thành `confirmed`.
  "Phương trình đánh giá được" = chỉ có vế cộng, hoặc mọi vế trừ đã có `sign` trong `dim_account`.
- **Kỳ không qua DQ** (`dq_balance_ok = false`, `period_check_ok = false` hoặc OCR không đọc được) xử lý bằng `config/manual_overrides.csv` (Cách B): người nhập tay số liệu **đọc từ chính PDF gốc trong Bronze**, ghi `source_doc_sha256` và số trang. Không nhập từ trí nhớ hay từ nguồn khác (vnstock, CafeF). Cột: `symbol, statement, fiscal_year, fiscal_quarter, period_kind, account_code, amount_vnd, ocr_value, source_doc_sha256, page, reason, entered_by, entered_at, suggested_by, status` (`ocr_value` = số OCR đọc sai, để đối chiếu). Chỉ áp dụng dòng có `status` trống (người nhập tay) hoặc `confirmed`. Override được áp dụng **trước** bước chọn bản thắng: thay giá trị bóc tự động của đúng ô `(source_doc_sha256, statement, account_code, period_kind)` và gắn `is_manual = true`. `is_manual` chỉ để truy vết, **không phải** một hạng trong thứ tự ưu tiên. Kỳ đó phải qua lại kiểm tra DQ rồi mới được sang Gold.

### Silver
- Dạng dài: `symbol, statement (B01/B02/B03), fiscal_year, fiscal_quarter, period_kind, account_code, item_name, amount_vnd, source_priority, source_lang, is_comparative, is_derived, is_manual, source_doc_sha256, dq_balance_ok`.
- Kỳ: `2026-Q3` → `fiscal_year=2026, fiscal_quarter=3`; báo cáo năm dùng `fiscal_quarter=0`. B02 quý giữ cả "quý này" và "lũy kế".
- Mã tài khoản: ưu tiên mã số in trên báo cáo; nếu thiếu thì ánh xạ qua `config/account_mapping.csv`. Dòng không ánh xạ được ghi ra `data/_state/unmapped_items.csv`; **không tự bịa mã**.
- Ghi bằng **Delta MERGE** theo khóa `(symbol, statement, fiscal_year, fiscal_quarter, period_kind, account_code)`. Bản thắng chọn theo thứ tự, tiêu chí trước quyết định trước:
  1. `is_comparative = false` (số gốc của chính kỳ đó) thắng `true` (số lấy từ cột kỳ trước của báo cáo kỳ sau);
  2. `is_derived = false` (số in trên báo cáo) thắng `true` (số tính ra từ các kỳ khác, vd. Q2 = H1 − Q1);
  3. `source_lang = 'vi-VN'` thắng `'en-US'` (en-US chỉ dùng khi kỳ đó không có bản vi-VN);
  4. `source_priority` cao hơn thắng: kiểm toán (3) > soát xét (2) > tự lập (1).
  Không bao giờ để bản xếp hạng thấp hơn ghi đè bản xếp hạng cao hơn **trong cùng một lượt nạp** (`merge_silver(full=False)` giữ điều kiện hạng); dựng lại toàn bộ (`full=True`) thì nguồn là sự thật đầy đủ, dòng cũ đã bị loại được thay.
  **Ngoại lệ Q4 (quyết định 13/11/2026):** khi đã có BCTC năm (kiểm toán), mọi chỉ tiêu B02 cộng được theo thời gian của Q4 = năm − 9T (`is_derived`), xếp trên số Q4 in trên BCTC quý 4 tự lập (`AUDITED_Q4_BONUS`, giữa tiêu chí 1 và 2), để bốn quý cộng lại bằng năm. Số Q4 tự lập giữ ở `fs_extracted_raw`; chênh lệch lũy kế Q4 tự lập − năm kiểm toán theo từng mã ghi ở mục "Điều chỉnh kiểm toán" của `docs/dq_report.md`.
- **Lũy kế 6 tháng lệch giữa BCTC bán niên và BCTC Q2:** bản đúng mọi phương trình B02 nội bộ chứa các mã lệch thắng (ô của bản thua `conflict_lost`, không vào Silver; Q2 thua thì cả cột quý này); hòa thì theo thứ tự ưu tiên (mức đảm bảo cao hơn thắng, chênh ghi ở mục "Điều chỉnh soát xét"); cùng mức mới sinh pending `suggested_by = doc_conflict` cho cả hai tài liệu. Ô của bản thua vẫn là bằng chứng xác nhận chéo nhưng không được chọn làm bản thắng.
- **EPS (B02 70, 71)** phải cùng bậc với LNST công ty mẹ / (vốn góp 411 / mệnh giá 10.000): ngoài [1/5, 5] lần thì loại (`implausible`), không coi là mâu thuẫn.
- **Xác nhận chéo giữa tài liệu:** ô B01/B02 được ≥ 2 tài liệu BCTC độc lập đọc khớp (kể cả cột kỳ trước của báo cáo sau), hoặc 1 tài liệu + phép tính lũy kế(q) = lũy kế(q−1) + quý(q) từ các tài liệu khác (vd. H1 = 9T − Q3 của BCTC Q3) → trusted và `gold_eligible` kể cả khi cột trượt critical, `gold_basis = cross_doc_consensus` (khác: `critical`, `derived`). Ô đã được nhiều tài liệu BCTC khớp thì không sinh gợi ý từ báo cáo tháng (số sơ bộ).
- **Số so sánh (`is_comparative = true`)** chỉ dùng để lấp kỳ không có tài liệu gốc (xem `docs/limitations.md`): lấy từ cột kỳ trước của báo cáo kỳ sau, vd. năm 2015 từ cột "năm trước" của BCTC FY2016; B02 Q1/2019 từ cột "quý này năm trước" của BCTC Q1/2020. B01 chỉ lấy được cho ngày cuối năm (cột "đầu năm"/"số đầu kỳ"); **không suy ra B01 giữa năm** (vd. B01 31/03/2019). Không ghi đè số gốc khi số gốc có sẵn (giữ số "as reported").
- Khi có cả số gốc và số so sánh của cùng ô mà lệch nhau > 0,1% thì ghi vào `docs/dq_report.md` mục **"Kỳ bị trình bày lại"** (kỳ, mã số, số gốc, số so sánh, tài liệu nguồn). Số gốc vẫn thắng.
- **Q2/2017:** B02 = bán niên 2017 (cột so sánh của BCTC bán niên 2018, en) − Q1/2017 (en); gắn `is_comparative = true` và `is_derived = true`. Không có B01 30/06/2017.
- Bản en-US: ánh xạ chỉ tiêu theo cột "Code"/"Mã số" như bản vi-VN; tên chỉ tiêu tiếng Anh đưa vào `account_mapping.csv` nếu thiếu mã.
- Đối soát 10–15 chỉ tiêu chính với `vnstock`; chênh > 0,1% thì ghi vào báo cáo DQ. **Dữ liệu vnstock không bao giờ được ghi vào Gold.** Khi vnstock chưa cài được (gỡ khỏi PyPI 11/2026): đối soát B02 mã 10 và 60 với báo cáo KQKD tháng lũy kế 3/6/9/12 của MWG (`src/quality/monthly_check.py`, kiểm tra có mặt sau khi làm tròn tỷ đồng), chỉ ghi báo cáo DQ.

### Gold (Star Schema)
- `fact_financial`: grain = 1 chỉ tiêu × 1 kỳ × 1 phạm vi. Số hợp nhất dùng `chain_key = 'MWG_TOTAL'`, `scope = 'consolidated'`; số theo chuỗi dùng `scope = 'segment'`. **Không bao giờ cộng lẫn hai scope.**
- `dim_date`, `dim_account`, `dim_chain` ghi đè mỗi lần chạy. `fact_financial` ghi Delta, partition `fiscal_year`.
- View KPI trong DuckDB (`v_kpi_quarter`, ...): biên LN gộp, biên LN ròng, ROE, ROA, số ngày tồn kho, YoY/QoQ, tỷ trọng doanh thu theo chuỗi. Luôn dùng `NULLIF(mẫu, 0)` khi chia.

### Dashboard
- Mở DuckDB ở chế độ `read_only=True`. Không chạy logic transform trong dashboard.
- Có `data/gold` thì đọc `data/gold/mwg.duckdb`; không có (Streamlit Community Cloud) thì đọc bản chụp `dashboard/snapshot/mwg.duckdb` do `silver_to_gold` xuất (bảng thật + view KPI). `data/gold` không commit.
- KPI dùng số dư bình quân đầu kỳ – cuối kỳ; thiếu số đầu kỳ thì dùng số dư cuối kỳ (`kpi_method = ending_balance`), dashboard ghi chú phương pháp.
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
- Sửa một lỗi/vấn đề đáng kể (bug, lỗi dữ liệu nguồn, lỗi OCR có hệ thống, đổi quy tắc DQ) thì thêm một mục `DBG-xx` vào `docs/debug_log.md` **trong cùng commit** (mẫu: giai đoạn/tầng, triệu chứng, nguyên nhân gốc, cách phát hiện, cách xử lý, kiểm chứng, commit, bài học); cập nhật bảng tóm tắt và bảng chỉ số qua các lượt chạy. Không có bằng chứng thì ghi "chưa xác minh".

## 8. Lộ trình (10 tuần) – để biết đang ở giai đoạn nào

| Tuần | Giai đoạn | Kết quả cần có |
|---|---|---|
| 1 | Khởi động & khảo sát mwg.vn | `sources.yaml` đầy đủ, ảnh chụp điều khoản sử dụng |
| 2–3 | Crawler + Bronze | Incremental chạy đúng, manifest khớp số file |
| 4–5 | Bóc tách PDF | OCR cho bản scan; ≥ 95% kỳ qua kiểm tra phương trình kế toán và đối chiếu ngày báo cáo; kỳ lỗi xử lý qua `manual_overrides.csv` |
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
