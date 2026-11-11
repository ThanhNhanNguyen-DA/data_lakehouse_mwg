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

## 2. Link bất thường trên nguồn (một URL cho hai kỳ)

Crawler ghi vào `data/_state/link_anomalies.csv` mỗi khi một mục trong danh sách của MWG trỏ tới URL đã lưu cho một kỳ khác. File trùng không được tải lại. Kỳ ghi trong tiêu đề của mục bị lỗi coi như **không có tài liệu gốc**, xem `docs/limitations.md`.

| Tiêu đề trên mwg.vn | Kỳ theo tiêu đề | Kỳ của file thật (trong manifest) | URL | Phát hiện |
|---|---|---|---|---|
| Interim Audited Consolidate FS 1H17 | 2017-H1 | 2017-Q3 | `bhxcdn.vn/.../2018/01/IC-FS-30.09.2017-consolE2.pdf` | 2026-11-11 |
| YTD May 2017 report | 2017-M05 | 2018-M05 | `bhxcdn.vn/.../2018/06/Monthly-report-YTD-May-18-EN.pdf` | 2026-11-11 |

Tên file (`30.09.2017`, `May-18`) cho thấy file thuộc kỳ ghi trong manifest. Điều này sẽ được xác nhận bằng ngày báo cáo đọc trong PDF ở bước bóc tách (tuần 4–5).

Giới hạn: crawler chỉ phát hiện được khi mục đúng được xử lý trước mục sai. Nếu mục sai được gặp trước, file sẽ bị lưu nhầm kỳ. Bước đối chiếu ngày báo cáo ở tuần 4–5 sẽ bắt trường hợp này.

## 3. Đối soát với vnstock (tuần 6)

Chưa thực hiện. Gói `vnstock` không còn trên PyPI, xem ghi chú trong `requirements.txt`.
