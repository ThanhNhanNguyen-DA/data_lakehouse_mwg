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

## 2. Đối soát với vnstock (tuần 6)

Chưa thực hiện. Gói `vnstock` không còn trên PyPI, xem ghi chú trong `requirements.txt`.
