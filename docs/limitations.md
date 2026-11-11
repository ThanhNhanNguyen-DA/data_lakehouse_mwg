# Phạm vi và giới hạn dữ liệu

Cập nhật: 11/11/2026, sau lần backfill vi-VN và en-US. Manifest có 161 file (101 vi-VN, 60 en-US); `download_errors.csv` có 72 link chết (69 vi-VN, 3 en-US). Đã kiểm tra: mọi file trong manifest có trên đĩa, đúng sha256, có `meta.json`.

## 1. Nguyên nhân thiếu tài liệu

Trang mwg.vn đã chuyển từ WordPress sang nền tảng mới. Tài liệu cũ vẫn được liệt kê trong API nhưng link vẫn trỏ về `mwg.vn/wp-content/uploads/...`, và tất cả các link này trả về **404**. Với bản vi-VN, việc này làm mất 56 tài liệu (BCTC hợp nhất và báo cáo tháng) cùng 13 ảnh báo cáo tháng (2017 – 01/2018).

Cách xử lý (xem `CLAUDE.md`):

- Dùng bản **en-US do chính MWG công bố** (link trên `bhxcdn.vn` còn sống) cho những kỳ không có bản vi-VN.
- Ở Silver, lấy **số so sánh** (cột kỳ trước của báo cáo kỳ sau, `is_comparative = true`) cho những kỳ không có tài liệu gốc.
- **Không dùng** Wayback Machine hay bản lưu của bên thứ ba.

## 2. Độ phủ BCTC hợp nhất theo kỳ

Ký hiệu: **vi** = bản vi-VN; **en** = bản en-US dự phòng; **—** = không có tài liệu gốc.
Chỉ liệt kê các kỳ MWG có đăng tài liệu. Giai đoạn 2014–2022, MWG không đăng riêng BCTC Q2 và Q4 (trừ bản en-US Q2/2022); Silver sẽ tính Q2 = bán niên − Q1 và Q4 = năm − 9 tháng.

| Năm | Q1 | H1 (bán niên) | Q2 | Q3 | Năm |
|---|---|---|---|---|---|
| 2014 | | — | — | vi ¹ | — |
| 2015 | — | — | | — | — |
| 2016 | en | en | | en | en |
| 2017 | en | — ⁴ | — ² | en | en |
| 2018 | en | en | | en | en |
| 2019 | — ³ | en | | en | en |
| 2020 | en | en | | vi (+ en đối chiếu) | vi (+ en đối chiếu) |
| 2021 | vi | vi | | vi | vi |
| 2022 | vi | vi | en | vi | vi |
| 2023–2025 | vi | vi | vi | vi | vi (cả Q4) |
| 2026 | vi | vi | vi | vi | (chưa đến kỳ) |

¹ Tiêu đề "Báo cáo tài chính Quý III năm 2014" không ghi hợp nhất hay riêng. Bước bóc tách phải xác nhận lại bằng tiêu đề trong PDF.
² Bản vi-VN "BCTC hợp nhất soát xét QII năm 2017" bị chết link. Nội dung nhiều khả năng là BCTC bán niên 2017.
³ Cả bản vi-VN và en-US ("Interim Consolidated FS 1Q19") đều nằm trên `mwg.vn/wp-content` (404).
⁴ **Link sai phía MWG:** mục en-US "Interim Audited Consolidate FS 1H17" trỏ tới file Q3/2017 (`IC-FS-30.09.2017-consolE2.pdf`), cùng URL với mục "3Q17". Crawler không tải trùng (URL đã có trong manifest với kỳ 2017-Q3), nên bán niên 2017 không có tài liệu gốc ở cả hai ngôn ngữ.

## 3. Kỳ chưa lấy lại được và khả năng lấp bằng số so sánh

Theo quy tắc trong `CLAUDE.md`: B02 và B03 lấy được từ cột "kỳ này năm trước"/"lũy kế năm trước" của báo cáo cùng kỳ năm sau. B01 chỉ lấy được cho ngày **cuối năm** (cột "số đầu năm" của báo cáo năm sau). Không suy ra B01 giữa năm.

| Kỳ thiếu | B01 | B02 / B03 | Nguồn số so sánh |
|---|---|---|---|
| FY2015 | ✓ (31/12/2015) | ✓ | BCTC FY2016 (en) |
| Q1/2015 | ✗ | ✓ | BCTC Q1/2016 (en) |
| H1/2015 | ✗ | ✓ | BCTC bán niên 2016 (en) |
| Q3/2015 | ✗ | ✓ (quý và lũy kế 9 tháng) | BCTC Q3/2016 (en) |
| Q1/2019 | ✗ | ✓ | BCTC Q1/2020 (en) |
| H1/2017 | ✗ | ✓ (lũy kế 6 tháng) | BCTC bán niên 2018 (en) |
| FY2014 | ✗ | ✗ | Cần BCTC 2015, mà BCTC 2015 cũng thiếu |
| H1/2014, Q2/2014 | ✗ | ✗ | Cần BCTC 2015, mà BCTC 2015 cũng thiếu |
| Q2/2017 (bản vi "QII") | ✗ | ✓ (`is_comparative` + `is_derived`) | B02 = H1/2017 (cột so sánh BCTC bán niên 2018, en) − Q1/2017 (en) |

Các kỳ đánh ✗ sẽ không có trong Gold. Dashboard cần hiển thị khoảng trống, không nội suy.

## 4. Báo cáo kết quả kinh doanh tháng

- **Bản vi-VN không lấy được:** 01/2017 → 08/2020 (44 kỳ). Tất cả nằm trên `mwg.vn/wp-content` (404), kể cả 13 ảnh của 2017 – 01/2018.
- **Lấp bằng bản en-US** ("YTD <tháng> <năm> report", `fetch_reason = fallback`): 41/44 kỳ (11 file `.jpg` năm 2017, cần OCR).
- **Vẫn thiếu:**
  - 01/2019 và 02/2019: bản en-US cũng nằm trên `wp-content` (404). Có thể dùng 03/2019 (lũy kế 3 tháng) để có số quý 1, nhưng không tách được từng tháng.
  - 05/2017: **link sai phía MWG**. Mục en-US "YTD May 2017 report" trỏ tới file tháng 5/2018 (`Monthly-report-YTD-May-18-EN.pdf`), cùng URL với mục "YTD May 2018 report".
- Báo cáo tháng là số lũy kế từ đầu năm. Nội dung là bản tóm tắt (doanh thu, số cửa hàng theo chuỗi), không phải BCTC, nên không áp dụng quy tắc `is_comparative`.

## 5. Giới hạn khác

- **PDF scan:** **cả 19 BCTC hợp nhất bản en-US (2016–2020, Q2/2022) đều là bản scan** (`has_text_layer = false`), nên giai đoạn 2016–2020 bắt buộc phải OCR. BCTC hợp nhất Q3/2026 (vi-VN) cũng là bản scan. Báo cáo tháng en-US (41 file): 29 PDF có lớp text, 1 PDF scan (01/2018), 11 ảnh `.jpg` (2017). 101 file vi-VN tải trước ngày 11/11/2026 chưa có giá trị `has_text_layer`.
- **Số liệu en-US** được coi là tương đương bản vi-VN. Mức độ khớp được kiểm tra trên Q3/2020 và FY2020, xem `docs/dq_report.md`.
- **Mức tin cậy theo tiêu đề:** tiêu đề en-US ghi "Audited" cả cho BCTC quý và bán niên đã soát xét, nên `assurance_level` của bản en-US không dựa vào chữ này (FY = 3, 1H = 2, quý ≤ 2).
