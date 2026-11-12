# MWG Financial Data Lakehouse

Data Lakehouse cho báo cáo tài chính của CTCP Đầu tư Thế Giới Di Động (MWG): crawl tài liệu gốc trên
mwg.vn → Bronze → Silver → Gold → Dashboard, chi phí 0 đồng. Quy tắc chi tiết: `CLAUDE.md`.
Giới hạn dữ liệu: `docs/limitations.md`. Chất lượng dữ liệu: `docs/dq_report.md`.

## 1. Cài môi trường Python 3.11

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
# Máy không có pip/python3-venv (vd. WSL): uv venv --python 3.11 .venv && uv pip install -r requirements.txt
```

## 2. Cài Tesseract OCR (bắt buộc cho bước bóc tách)

Mọi BCTC hợp nhất của MWG là PDF scan, nên bóc tách cần Tesseract kèm gói ngôn ngữ **vie** và **eng**.

### Windows

1. Tải bộ cài Tesseract 5 cho Windows của UB Mannheim: <https://github.com/UB-Mannheim/tesseract/wiki>
   (file `tesseract-ocr-w64-setup-5.x.x.exe`).
2. Khi cài, ở bước **Choose Components → Additional language data (download)**, đánh dấu **Vietnamese**
   (English có sẵn). Nếu quên, tải `vie.traineddata` từ <https://github.com/tesseract-ocr/tessdata>
   và chép vào `C:\Program Files\Tesseract-OCR\tessdata\`.
3. Thêm `C:\Program Files\Tesseract-OCR` vào biến môi trường `PATH`, **hoặc** đặt biến
   `TESSERACT_CMD` trỏ thẳng tới file exe:
   ```powershell
   setx TESSERACT_CMD "C:\Program Files\Tesseract-OCR\tesseract.exe"
   ```
   Mở lại terminal sau khi đổi biến môi trường.
4. Kiểm tra:
   ```powershell
   tesseract --version
   tesseract --list-langs     # phải có: eng, vie
   ```

### Ubuntu / WSL

```bash
sudo apt install -y tesseract-ocr tesseract-ocr-vie tesseract-ocr-eng
tesseract --list-langs
```

## 3. Chạy pipeline

```bash
python run_pipeline.py --backfill    # lần đầu: crawl toàn bộ lịch sử (chậm, có nghỉ giữa request) -> bóc tách -> Silver -> Gold
python run_pipeline.py               # incremental: không có tài liệu mới thì dừng ("Không có tài liệu mới")
python run_pipeline.py --force       # chạy lại bóc tách/Silver/Gold (vd. sau khi xác nhận ô trong manual_overrides.csv)
python run_pipeline.py --no-crawl --force   # máy offline: dựng lại từ Bronze sẵn có
```

Từng bước riêng:

```bash
python -m src.ingestion.mwg_crawler --dry-run     # xem trước tài liệu sẽ tải
python -m src.extraction.run_extraction           # OCR + bóc B01/B02/B03 + DQ -> silver/fs_extracted_raw, docs/dq_report.md
python -m src.transform.bronze_to_silver          # -> silver/fin_statement_long (Delta MERGE)
python -m src.transform.silver_to_gold            # -> gold/dim_*, gold/fact_financial (Delta) + gold/mwg.duckdb
python tools/demo_time_travel.py                  # demo Delta time travel (bảng tạm, không đụng Silver thật)
pytest -q
```

Kết quả OCR được cache trong `data/_cache/ocr/` theo sha256 của file, chạy lại không OCR lại (lần chạy đầy đủ
~1 phút khi đã có cache). Test OCR trên PDF mẫu (`tests/test_fixtures_ocr.py`) tự bỏ qua khi máy chưa có Tesseract;
`tests/test_kpi_pdf.py` (KPI khớp số đọc trên PDF gốc) bỏ qua khi chưa dựng Gold.

Các lớp dữ liệu:

| Lớp | Bảng | Ghi chú |
|---|---|---|
| Bronze | `data/bronze/source=mwg_ir/...` + `data/_state/manifest.parquet` | PDF/ảnh gốc, append-only |
| Silver | `data/silver/fs_extracted_raw` | mọi ô bóc được, cờ DQ, ghi đè mỗi lần chạy |
| Silver | `data/silver/fin_statement_long` | dạng dài theo kỳ thật, chọn bản thắng bằng Delta MERGE |
| Gold | `data/gold/fact_financial`, `dim_date`, `dim_account`, `dim_chain` | chỉ ô qua DQ, partition `fiscal_year` |
| Gold | `data/gold/mwg.duckdb` | chỉ chứa view: `v_kpi_quarter`, `v_kpi_year`, `v_coverage`, `v_lineage`, ... |

## 4. Mở dashboard

```bash
streamlit run dashboard/app.py
```

Mở <http://localhost:8501>. Dashboard mở `data/gold/mwg.duckdb` ở chế độ `read_only` và tự chuyển thư mục làm việc về
gốc repo (view dùng đường dẫn tương đối). Lần đầu DuckDB tự tải extension `delta` (cần mạng một lần).

- **WSL:** chạy lệnh trên trong WSL rồi mở <http://localhost:8501> bằng trình duyệt Windows (WSL2 tự chuyển tiếp
  localhost). Nếu không vào được: `streamlit run dashboard/app.py --server.address 0.0.0.0` và mở bằng IP của WSL
  (`hostname -I`).
- **Windows (PowerShell):** `.venv\Scripts\activate` rồi `streamlit run dashboard\app.py`. Có thể dùng chung thư mục
  repo với WSL (`\\wsl$\Ubuntu\home\<user>\data_lakehouse_mwg`), nhưng venv phải tạo riêng cho Windows.
- Trang "Lineage" hiện ảnh trang PDF khi file Bronze có trên máy.

Chụp ảnh các trang (kiểm tra headless, ảnh ở `docs/screenshots/`):

```bash
python -m playwright install chromium
streamlit run dashboard/app.py --server.headless true --server.port 8599 &
python tools/screenshot_dashboard.py --url http://localhost:8599
```

WSL thiếu `libasound.so.2` mà không có sudo: `apt-get download libasound2t64`, `dpkg-deb -x <file>.deb <thư mục>`,
rồi `export LD_LIBRARY_PATH=<thư mục>/usr/lib/x86_64-linux-gnu`.

## 5. Tự động hóa (GitHub Actions) và deploy dashboard (Streamlit Community Cloud)

**Dữ liệu nào nằm trong git:** manifest + `*.meta.json` của Bronze, `data/silver/fin_statement_long`, `data/gold`
(kể cả `mwg.duckdb`, chỉ chứa view) — khoảng 13 MB. **Không** có PDF Bronze (~800 MB), cache OCR và
`data/silver/fs_extracted_raw`. Clone mới muốn chạy lại pipeline thì tải lại PDF theo manifest (kiểm sha256, chỉ ghi
khi khớp; tự chạy trong `run_pipeline.py`):

```bash
python -m src.ingestion.mwg_crawler --restore
```

**GitHub Actions** (`.github/workflows/crawl.yml`, repo public nên miễn phí): 08:00 thứ Hai (giờ VN) crawl incremental;
có tài liệu mới thì tải lại Bronze còn thiếu, OCR (Tesseract 5.3.4 trên `ubuntu-24.04`, cùng bản với máy phát triển),
Silver, Gold, chạy `pytest` (gồm KPI khớp số đọc trên PDF gốc), rồi commit dữ liệu bằng `GITHUB_TOKEN`. PDF và cache
OCR được giữ giữa các lần chạy bằng `actions/cache`; lần chạy đầu cache trống nên lâu (tải ~800 MB có nghỉ giữa
request + OCR toàn bộ, giới hạn 340 phút). Chạy tay: tab **Actions → crawl → Run workflow** (tick `force` để dựng lại
Silver/Gold). Lịch cron chỉ chạy trên **nhánh mặc định** (`master`): cần merge nhánh này vào `master`.

**Streamlit Community Cloud:**
1. Đẩy code lên GitHub và merge vào `master`.
2. Vào <https://share.streamlit.io>, đăng nhập bằng GitHub → **Create app** → **Deploy a public app from GitHub**.
3. Repository `<tài khoản>/data_lakehouse_mwg`, branch `master`, main file path `dashboard/app.py`.
4. **Advanced settings → Python 3.11**. Cloud tự dùng `dashboard/requirements.txt` (cạnh `app.py`, không kèm
   OCR/Paddle). Không cần secret.
5. Deploy. Mỗi lần Actions commit Gold mới, app tự cập nhật. Trang Lineage trên Cloud không có ảnh PDF (Bronze không
   lên git), chỉ hiện link gốc.

## 6. Sửa tay khi OCR sai

Kỳ không qua DQ được sửa bằng `config/manual_overrides.csv`: đọc số đúng **từ chính PDF gốc trong
`data/bronze/`**, ghi `source_doc_sha256`, số trang, giá trị OCR sai (`ocr_value`) và lý do.
Chạy lại `run_extraction` để kiểm tra DQ của kỳ đó.
Ô gợi ý tự động (`status = pending`) chỉ được áp dụng sau khi đổi thành `confirmed`; xem nhanh bằng
`python -m src.quality.review_overrides` (trang `review/overrides_review.html`), rồi `python run_pipeline.py --force`.

Quyết định thiết kế: `docs/decisions.md`. Nhật ký lỗi: `docs/debug_log.md`.
