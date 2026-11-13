# Quyết định thiết kế (tự chọn khi làm liên tục, 13/11/2026)

Mỗi mục: quyết định, lý do, cách đảo lại nếu cần.

## Phần A – Chốt OCR

- **A1. Chặn B02 theo cột.** Ô B02 vào Gold khi ô trusted và mọi phương trình critical B02 *của chính cột đó*
  (`period_kind`) đúng; cột không có phương trình critical nào kiểm được thì không qua (không coi "không kiểm" là
  "qua"). Điều kiện cấp báo cáo (có trang B02, ngày/kỳ khớp, nhãn cột TT99 chứng minh được) vẫn áp cho mọi cột.
- **A2. Giá trị gợi ý từ đối soát báo cáo tháng chỉ lấy từ OCR lại ô trên PDF BCTC**, không tự thay chữ số theo báo
  cáo tháng: báo cáo tháng làm tròn tỷ đồng nên không cho được số VND, và CLAUDE.md cấm số không đọc từ PDF gốc. Khi
  đọc lại vẫn ra số cũ thì ghi vào bảng đối soát ("nghi báo cáo tháng") thay vì sinh pending.
- **A3. Tool một lần (`build_dim_account.py`) bọc `main()`** để import được trong test; cách gọi dòng lệnh không đổi.

## Phần B – Silver `fin_statement_long`

- **Khóa kỳ.** B01 là số tại một ngày: `period_kind = instant`, `fiscal_quarter` = quý của ngày (31/12 → 4), để BCTC
  năm và BCTC Q4 (cùng ngày 31/12) cùng khóa và được xếp hạng với nhau (bản kiểm toán thắng). B02/B03:
  `quarter` (3 tháng), `ytd` (lũy kế 6 hoặc 9 tháng), `year` (`fiscal_quarter = 0`, gồm cả cột lũy kế 12 tháng của BCTC
  Q4). Lũy kế Q1 quy về `quarter` (bằng nhau). Mở rộng so với CLAUDE.md (chỉ nói "năm dùng fiscal_quarter = 0").
- **Kỳ suy ra thêm Q3 = 9T − H1** (ngoài Q2 = H1 − Q1, Q4 = năm − 9T): BCTC Q3 2017–2022 chỉ in cột lũy kế, thiếu Q3
  thì chuỗi quý thủng một quý mỗi năm. "Mã cộng được" hiểu là cộng được theo thời gian: mọi mã B02/B03 trừ lãi trên cổ
  phiếu (B02 70, 71) và tiền đầu/cuối kỳ (B03 60, 70); cột `is_additive` của `dim_account` mô tả quan hệ cha–con (10,
  20, 60 không có cha nên để trống), không dùng được cho việc này.
- **Dấu.** Mã chuẩn có `sign = -1` lưu số âm (DBG-34).
- **Xếp hạng trong MERGE.** `rank_score = (không so sánh)·1000 + (không suy ra)·100 + (vi-VN)·10 + source_priority`;
  MERGE chỉ cập nhật khi `s.rank_score >= t.rank_score` và nội dung khác (`row_hash`). Hòa hạng: ô gold_eligible rồi
  sha256 nhỏ hơn (ổn định giữa các lần chạy). Chạy đầy đủ thì xóa khóa không còn trong nguồn
  (`when_not_matched_by_source_delete`); nạp một phần (demo) thì không.
- **Override.** `status = confirmed` (hoặc trống) đã được áp ở bước bóc tách, trước khi xếp hạng; Silver chỉ mang cờ
  `is_manual`. Pending bỏ qua.
- **Lọc vào Silver.** `period_check_ok = false` và `period = pending`: loại cả báo cáo. `columns_ok = false`: chỉ loại
  B02 của báo cáo đó (B01/B03 không phụ thuộc nhãn cột B02). Mã OCR sai (không có trong `dim_account`) và mã TT99 chưa
  map: ghi `unmapped_items.csv` kèm lý do từng dòng. Ô TT99 `semantic_break` rơi vào ngày còn theo TT200 (đầu năm 2026):
  loại khỏi Silver (giữ ở `fs_extracted_raw`); mọi ô TT99 của mã đó mang `semantic_break = true`.
- **Ràng buộc Delta** (CHECK): `statement`, `fiscal_quarter` 0–4, `period_kind`, `amount_vnd` NOT NULL, `source_lang`.
  Schema được ép bằng `pyarrow` trước khi ghi (`to_arrow`), Delta từ chối append sai kiểu.
- **Demo time travel** chạy trên bảng tạm, không đụng Silver thật: nạp Silver không có BCTC năm 2025 rồi MERGE thêm bản
  kiểm toán, so hai phiên bản.
- **Dữ liệu trong git.** Không commit thêm file Delta của `data/silver`, `data/gold` (sinh lại được, lớn dần theo phiên
  bản). Repo hiện vẫn theo dõi các file `fs_extracted_raw` đã commit ở `e67711b`; quyết định cuối ở phần deploy.

## Phần C – Gold

- **fact_financial** chỉ nhận ô `gold_eligible` của Silver (ô suy ra chỉ eligible khi cả hai vế eligible), B01/B02.
  Grain kiểm trước khi ghi (trùng khóa → không ghi). Ghi đè mỗi lần chạy (Gold dựng lại toàn bộ từ Silver), partition
  `fiscal_year`; lịch sử phiên bản vẫn còn trong Delta log. CHECK: `scope ∈ {consolidated, segment}`, consolidated ⇔
  `chain_key = MWG_TOTAL`, `amount_vnd` NOT NULL. Chưa có số theo chuỗi (chain_kpi để sau): bảng chỉ có scope
  consolidated, mọi view KPI lọc `scope = 'consolidated'`.
- **dim_date** gồm mọi ngày cuối quý trong khoảng năm của fact (kể cả quý không có số) để thấy khoảng trống.
  `dim_account` chỉ mã chuẩn TT200.
- **mwg.duckdb chỉ chứa view** (`delta_scan`/`read_parquet` theo đường dẫn tương đối từ gốc repo), tạo lại mỗi lần chạy
  qua file tạm rồi thay (view hỏng thì giữ file cũ). Người đọc phải mở với thư mục làm việc = gốc repo (dashboard tự
  `chdir`). Lý do: chạy được cả trên WSL lẫn Windows, không chép dữ liệu hai lần.
- **KPI.** ROE/ROA quý năm hóa ×4 trên bình quân đầu/cuối quý; ROE/ROA năm trên bình quân 31/12 năm trước và năm nay;
  thiếu một đầu thì NULL (không lấp bằng số chưa trusted). Số ngày tồn kho = bình quân 140 / |giá vốn| × 91,25 (quý)
  hoặc × 365 (năm). YoY/QoQ nối với đúng kỳ liền trước (không dùng `lag`, để kỳ thiếu không làm lệch). Mọi phép chia
  dùng `NULLIF`.
- **semantic_break.** Không có KPI tăng trưởng nào dùng mã 120/130; view `v_balance_structure` có `series_key` tách
  chuỗi TT99 của mã đổi nội dung để biểu đồ không nối liền; cột `has_semantic_break` cho dashboard ghi chú.
- **Đối chiếu KPI với PDF (DoD).** Số của FY2024 và Q3/2025 đọc trực tiếp trên ảnh trang PDF Bronze, ghi kèm sha256 và
  trang trong `tests/fixtures/kpi_pdf_values.csv`; `tests/test_kpi_pdf.py` so với `v_kpi_year`/`v_kpi_quarter`.

## Phần D – Dashboard và pipeline

- **Dashboard một file** (`dashboard/app.py`, chọn trang ở sidebar) thay vì multipage: ít file, đủ 6 trang. Kỳ thiếu
  số giữ ô trống trên trục (không nội suy, đường đứt quãng). Màu theo thứ tự categorical cố định đã kiểm định CVD;
  mỗi biểu đồ một trục y (không dual-axis).
- **Ghi chú "đổi chuẩn TT99"** chỉ vẽ trên biểu đồ có mã `semantic_break` (cơ cấu tài sản, mã 120/130) tại kỳ TT99
  đầu tiên trong Gold; biểu đồ doanh thu/LNST/biên không dùng mã đổi nội dung nên không vẽ.
- **Lineage** hiện ảnh trang PDF bằng `pypdfium2` khi file Bronze có trên máy; đây là hiển thị, không phải transform.
- **run_pipeline.py** dừng sau crawl khi không có tài liệu mới (CLAUDE.md); `--force` chạy lại bóc tách/Silver/Gold,
  `--no-crawl` cho máy offline. Bóc tách luôn chạy cho mọi BCTC (dùng cache OCR, ~20 giây), không chỉ file mới, vì
  bỏ phiếu giữa các lần in lại và đối chiếu đầu năm/cuối năm cần mọi báo cáo.
- **Ảnh chụp headless** bằng Playwright (có trong stack CLAUDE.md). WSL thiếu `libasound2` và không có sudo: lấy gói
  `.deb` bằng `apt-get download`, giải nén vào thư mục tạm và đặt `LD_LIBRARY_PATH` (ghi trong README).

## Tuần 9 – GitHub Actions và Streamlit Cloud

- **Không đưa PDF Bronze và cache OCR lên repo public** (giữ quyết định trong `.gitignore`: ~800 MB, và điều khoản
  mwg.vn chỉ cho dùng cá nhân/học thuật, không phân phối lại). Runner giữ chúng bằng `actions/cache` (lưu cả khi job
  lỗi, để lần sau không tải/OCR lại); cache bị xóa sau 7 ngày không dùng thì lần sau tải lại theo manifest.
- **Tải lại Bronze theo manifest** (`restore_missing`): chỉ ghi khi sha256 trùng manifest, nên Bronze trên runner/clone
  giống hệt bản gốc; nội dung nguồn đã đổi thì không ghi và báo lại. `run_pipeline.py` dừng (exit 1) nếu vẫn thiếu
  file: bóc tách thiếu báo cáo sẽ xóa các kỳ đó khỏi Silver/Gold khi MERGE đầy đủ.
- **Pipeline trên CI chỉ chạy bóc tách khi có tài liệu mới** (như local). Lần chạy không có tài liệu mới chỉ tốn vài
  phút crawl.
- **Kiểm thử trước khi commit dữ liệu:** `pytest` (gồm KPI khớp PDF) chạy sau pipeline; trượt thì job dừng, không commit.
- **Gold commit lên git, vacuum mỗi lần dựng** (chỉ giữ file của phiên bản hiện tại; Delta log vẫn ghi lịch sử thao
  tác). Time travel dùng ở Silver `fin_statement_long` (giữ đủ phiên bản, MERGE chỉ thêm file khi có thay đổi).
- **Bỏ theo dõi `data/silver/fs_extracted_raw`** (`git rm --cached`, file vẫn ở máy): bảng sinh lại mỗi lần bóc tách,
  lớn dần theo phiên bản, và trước đây chỉ được commit một phần (log v0–v4) nên bản clone thấy bảng không khớp máy
  local. Dashboard không cần bảng này.
- **Requirements riêng cho dashboard** (`dashboard/requirements.txt`): Streamlit Cloud cài gói cạnh `app.py`, tránh
  PaddlePaddle/Tesseract. Phiên bản trùng `requirements.txt`.

## Quyết định của người dùng 13/11/2026 (DBG-36…41)

- **Q4 = năm kiểm toán − 9T** khi đã có BCTC năm (phương án b): mọi chỉ tiêu B02 cộng được theo thời gian; số Q4 tự lập
  giữ ở `fs_extracted_raw`, chênh lệch ghi ở mục "Điều chỉnh kiểm toán". Đảo lại: đặt `AUDITED_Q4_BONUS = 0`.
- **Xác nhận chéo giữa tài liệu** (`gold_basis = cross_doc_consensus`): ≥ 2 tài liệu khớp hoặc 1 tài liệu + phép tính
  lũy kế từ tài liệu khác → vào Gold kể cả khi cột trượt critical; không thắng số mâu thuẫn đã qua critical. Báo cáo
  tháng (số sơ bộ) không sinh gợi ý cho ô đã đồng thuận.
- **semantic_break cho TT99 135**; mã đã ghép biến động > 20% giữa 31/12/2025 và đầu năm 2026 vào danh sách chờ duyệt
  (`docs/dim_account_notes.md`).

## Quyết định của người dùng 13/11/2026 (DBG-41…43, deploy)

- **Khóa pipeline** `data/_state/pipeline.lock`; `--force` không crawl, `--force --crawl` để crawl kèm.
- **Lũy kế 6 tháng lệch giữa BCTC bán niên và BCTC Q2:** phân xử bằng phương trình B02 đánh giá được của từng tài liệu;
  hòa thì người dùng chọn (pending `doc_conflict`, crop cả hai PDF). Không tự chọn theo `source_priority` khi hòa.
- **KPI theo số dư cuối kỳ khi thiếu đầu kỳ** (`kpi_method = ending_balance`), dashboard ghi chú.
- **Streamlit Community Cloud đọc bản chụp** `dashboard/snapshot/mwg.duckdb` (bảng Gold + Silver + danh sách tài liệu
  vật chất hóa, view KPI, 1,8 MB, không cần extension delta, không chứa PDF), do `silver_to_gold.run()` xuất mỗi lần.
  Vì vậy **bỏ theo dõi `data/gold`** (`git rm --cached`, file vẫn ở máy; `.gitignore`), workflow commit
  `dashboard/snapshot` thay `data/gold`. Silver `fin_statement_long` vẫn commit (giữ lịch sử time travel giữa các lần
  chạy CI). Đảo lại: bỏ dòng `data/gold/*` trong `.gitignore` và thêm `data/gold` vào bước commit của workflow.

