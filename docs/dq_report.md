# Báo cáo chất lượng dữ liệu (DQ)

## 1. Đối chiếu bản vi-VN và en-US cùng kỳ

**Mục đích:** BCTC 2016–2020 phần lớn chỉ còn bản en-US (xem `docs/limitations.md`). Phần này kiểm tra xem số liệu bản en-US có giống bản vi-VN không, trên hai kỳ có đủ cả hai bản.

| Kỳ | Bản vi-VN | Bản en-US | Ghi chú |
|---|---|---|---|
| Q3/2020 | Báo Cáo Tài Chính Hợp Nhất Soát Xét Qúy 3 Năm 2020 | Audited Consolidated FS 3Q2020 | Bản en ghi "Audited", bản vi ghi "Soát Xét" |
| FY2020 | Báo Cáo Tài Chính Hợp Nhất Soát Xét Năm 2020 | Audited Consolidated FS FY2020 | Bản vi ghi "Soát Xét" dù là BCTC năm |

**Trạng thái:** chờ tải bản en-US (`fetch_reason = cross_check`) và chờ bóc tách (tuần 4–5).

**Tiêu chí:** cùng mã số TT200 thì số tiền phải bằng nhau tuyệt đối sau khi quy đổi về VND. Nếu lệch, ghi từng dòng vào bảng dưới và tìm nguyên nhân (lỗi OCR/bóc tách, khác đơn vị tính, khác cách trình bày).

| Kỳ | Báo cáo | Mã số | Chỉ tiêu | vi-VN (VND) | en-US (VND) | Chênh lệch | Nguyên nhân |
|---|---|---|---|---|---|---|---|
| | | | | | | | |

## 2. Link bất thường trên nguồn (một URL cho nhiều kỳ)

Crawler gom toàn bộ danh sách rồi nhóm theo URL. Một URL được MWG gắn cho nhiều kỳ khác nhau thì được tải **một lần** với `period = pending`. Mọi mục của URL đó được ghi vào `data/_state/link_anomalies.csv`. Kỳ thật được gán sau khi đọc ngày báo cáo trong PDF (tuần 4–5). Kỳ còn lại của nhóm coi như **không có tài liệu gốc**, xem `docs/limitations.md`.

| URL | Các mục trên mwg.vn (kỳ theo tiêu đề) | Đoán theo tên file | Kỳ sau khi đọc PDF |
|---|---|---|---|
| `bhxcdn.vn/.../2018/01/IC-FS-30.09.2017-consolE2.pdf` | "Interim Audited Consolidate FS 3Q17" (2017-Q3), "Interim Audited Consolidate FS 1H17" (2017-H1) | 2017-Q3 | chờ |
| `bhxcdn.vn/.../2018/06/Monthly-report-YTD-May-18-EN.pdf` | "YTD May 2018 report" (2018-M05), "YTD May 2017 report" (2017-M05) | 2018-M05 | chờ |

Phát hiện ngày 2026-11-11. Hai file này đã được tải trước khi có quy tắc nhóm theo URL. Tên file và `meta.json` trong Bronze vẫn ghi 2017Q3 và 2018M05; manifest đã chuyển sang `pending`.

## 3. Kỳ bị trình bày lại

Khi cùng một ô (kỳ, báo cáo, mã số) có cả số gốc (báo cáo của chính kỳ đó) và số so sánh (cột kỳ trước của báo cáo kỳ sau) mà lệch nhau > 0,1%, ghi vào bảng dưới. Silver vẫn dùng số gốc ("as reported").

| Kỳ | Báo cáo | Mã số | Số gốc (VND) | Số so sánh (VND) | Chênh lệch | Tài liệu gốc | Tài liệu so sánh |
|---|---|---|---|---|---|---|---|
| | | | | | | | |

Chưa có dữ liệu (cần Silver, tuần 6).

## 4. Đối soát với vnstock (tuần 6)

Chưa thực hiện. Gói `vnstock` không còn trên PyPI, xem ghi chú trong `requirements.txt`.
