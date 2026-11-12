# Kế hoạch Tuần 6–7: Silver → Gold → dashboard MVP

Lập ngày 12/11/2026, sau khi **dừng tối ưu OCR**. Từ đây số liệu OCR chỉ được cải thiện bằng cách người dùng
xác nhận ô trong `config/manual_overrides.csv` (trang duyệt: `python -m src.quality.review_overrides` →
`review/overrides_review.html`), không viết thêm heuristic OCR.

## Điểm xuất phát (bảng `data/silver/fs_extracted_raw`)

- 52 BCTC (2014-Q3 → 2026-Q3), 109 báo cáo tháng. Qua critical 28/52 (54%), ô trusted 60%, ô `gold_eligible` ≈ 3.900
  (B01 ≈ 3.300, B02 ≈ 650, B03 = 0 vì B03 chưa có phương trình kiểm).
- `period_kind`: B01 `closing/opening`; B02 năm/bán niên `current/prior`, B02 quý `q_current/q_prior/ytd_current/ytd_prior`;
  B03 `current/prior`.
- Đã có: `canonical_code` (mã TT200 chuẩn), `regime`, `lang`, `value_origin`, `is_trusted`, `passed_critical`, `gold_eligible`.
- Chưa có: `source_priority` trong bảng raw (nằm ở manifest `assurance_level`), `fiscal_year/fiscal_quarter` tách sẵn.

## Tuần 6: Silver `fin_statement_long`

**`src/transform/bronze_to_silver.py`** (tên giữ theo CLAUDE.md; thực tế đọc `fs_extracted_raw` + manifest):

1. **Chọn cột → dạng dài chuẩn.** `fiscal_year`, `fiscal_quarter` (năm = 0, H1 = 2 với `period_kind` riêng),
   `account_code` = `canonical_code` (mã chuẩn TT200; giữ `printed_code` + `regime` để truy vết),
   `source_priority` = `assurance_level` từ manifest, `source_lang` = `lang`.
2. **Quy kỳ của từng cột về kỳ thật** (bảng quy tắc, có test cho từng dòng):
   - B01 `closing` → ngày cuối kỳ của báo cáo; `opening` → 31/12 năm trước, `is_comparative = true`.
   - B02 quý `q_current` → quý đó; `ytd_current` → lũy kế (period_kind `ytd`); `q_prior`/`ytd_prior` → cùng kỳ năm trước,
     `is_comparative = true`.
   - B02 năm/bán niên `current` → năm/H1; `prior` → năm/H1 trước, `is_comparative = true`.
3. **Kỳ suy ra** (`is_derived = true`): Q4 = FY − 9T; Q2 = H1 − Q1 (khi không có BCTC Q2); Q2/2017 theo CLAUDE.md.
   Chỉ suy ra từ ô `gold_eligible`; kết quả không được là "trusted" nếu một vế không trusted.
4. **Lọc vào Silver:** loại tài liệu `period_check_ok = false`, `columns_ok = false`, `period = pending`. Giữ
   `dq_balance_ok`, `is_trusted`, `passed_critical` như cột để Gold lọc (Silver giữ đầy đủ để truy vết).
5. **Delta MERGE** theo khóa `(symbol, statement, fiscal_year, fiscal_quarter, period_kind, account_code)`; xếp hạng
   `is_comparative` ↑, `is_derived` ↑, `source_lang = vi-VN` trước, `source_priority` ↓. Viết hàm `rank_key()` thuần
   Python + test, dùng chung cho dedupe trước MERGE và điều kiện `WHEN MATCHED AND <mới hạng cao hơn>`.
6. **Kỳ bị trình bày lại:** chuyển phần so sánh số gốc vs số so sánh (> 0,1%) từ `run_extraction` sang Silver;
   ghi `docs/dq_report.md` mục 3. Số gốc thắng.
7. **unmapped_items.csv:** mã TT99 chưa có `canonical_code` (26 mã không khớp số + mã tổng) ghi ra file, không đưa sang
   Gold. Các tổng TT99 đổi nội dung (vd. 130: chênh 8.010 tỷ so với TT200 130 tại 31/12/2025) **không** được nối chuỗi
   thời gian với TT200 cho tới khi bảng map xác nhận.
8. **Đối soát vnstock:** gói đã gỡ khỏi PyPI (requirements.txt). Nếu không tìm được kênh chính thức trong tuần 6 thì
   ghi vào `docs/limitations.md` và bỏ qua; không thay bằng nguồn trả phí.
9. `chain_kpi` (báo cáo tháng): **để sau** theo thứ tự ưu tiên của CLAUDE.md.

Test: `rank_key`, quy kỳ từng `period_kind`, Q4 = FY − 9T, không ghi đè số gốc bằng số so sánh, MERGE chạy 2 lần
không đổi số dòng.

## Tuần 7: Gold + view KPI

**`src/transform/silver_to_gold.py`:**

1. `dim_account` từ `config/dim_account.csv` (chỉ dòng `regime = TT200`, vì Gold dùng mã chuẩn), `dim_date` (năm,
   quý, ngày cuối kỳ, nhãn hiển thị), `dim_chain` từ `config/dim_chain.csv`. Ghi đè mỗi lần chạy.
2. `fact_financial`: **chỉ ô `gold_eligible`** (trusted + báo cáo qua critical) và ô suy ra từ chúng;
   `chain_key = 'MWG_TOTAL'`, `scope = 'consolidated'`. Delta, partition `fiscal_year`.
3. `data/gold/mwg.duckdb`: view `delta_scan` trên các bảng Delta + `v_kpi_quarter`, `v_kpi_year`
   (biên LN gộp 20/10, biên LN ròng 60/10, ROE 60 / TB(410), ROA 60 / TB(270), số ngày tồn kho 140 / giá vốn × số ngày,
   YoY, QoQ), luôn `NULLIF(mẫu, 0)`. Kỳ thiếu ô thì KPI = NULL, không lấp bằng số chưa trusted.
4. `run_pipeline.py`: crawl → extraction → silver → gold; `--force` chạy lại Silver/Gold.

Test: grain không trùng khóa, không có hàng `scope` lẫn, KPI 2 kỳ khớp số tự tính trên PDF gốc (người dùng đọc số
từ PDF và điền vào fixture, không điền từ trí nhớ).

## Dashboard MVP (cuối tuần 7, kéo sang tuần 8 nếu thiếu thời gian)

`dashboard/app.py`: DuckDB `read_only=True`; 3 phần: (1) doanh thu thuần + LN sau thuế theo quý/năm (tỷ đồng),
(2) biên LN gộp/ròng, ROE/ROA, (3) bảng độ phủ dữ liệu: kỳ nào có/thiếu trong Gold và lý do (từ cờ DQ), để người xem
thấy rõ khoảng trống thay vì đường biểu đồ nối qua kỳ thiếu.

## Rủi ro đã biết

- **Độ phủ B02 thấp** (≈ 650 ô gold_eligible): KPI theo quý sẽ có nhiều kỳ NULL. Cách tăng duy nhất được phép: người
  dùng xác nhận ô pending / nhập tay từ PDF gốc. TT200 B02 30 và 60 chưa có công thức kiểm chắc chắn → ô 60 chỉ trusted
  khi nằm trong phương trình khác đúng hoặc bỏ phiếu.
- **Nhãn cột B02 Q2/2026 chưa chứng minh được** (TT99) → không vào Gold.
- **Bảng map TT99:** nên đổi mốc so số từ Q4/2025 sang BCTC năm 2025 đã kiểm toán (khớp 48 mã thay vì 33); TT99 261
  mâu thuẫn tên (251) và số (261), cần người dùng xác nhận. Dựng lại `dim_account` khi có bản TT202 chính thức trong
  `docs/reference/`.
- **2024 mã 153, 228:** đầu năm 2025 lệch cuối năm 2024 đúng một chữ số thừa → nhiều khả năng BCTC 2024 bị OCR sai,
  không phải trình bày lại; cần xem PDF gốc trước khi Gold dùng số 31/12/2024 của hai mã này.
