# Danh mục chỉ tiêu (`dim_account`) và bảng quy đổi mã TT99 → TT200

Dựng ngày 12/11/2026 bằng `tools/build_dim_account.py`, **chỉ từ văn bản trong `docs/reference/`**:

| Regime | Văn bản | Dùng cho |
|---|---|---|
| TT200 | `TVT-ProTrain-PL01-BCTCHN.doc` – Phụ lục 1 TT202/2014 (biểu mẫu BCTC hợp nhất, bản soạn lại của TVT-ProTrain) | mã, tên chỉ tiêu B01-DN/HN, B02-DN/HN; công thức 270, 440, 10, 20, 40, 50, 60 |
| TT200 | `phu-luc-2.doc` – Phụ lục 2 TT200/2014 | công thức cấp mục 100, 200, 300, 400 |
| TT99 | `phu-luc-4-thong-tu-99.pdf` – Phụ lục IV TT99/2025 (bản scan) | mã, tên B01-DN (tr. 1–5) và B02-DN (tr. 9) đọc từ bản scan; công thức cha–con từ phần hướng dẫn lập (tr. 88–130) |

Quy ước: `sign = -1` cho chỉ tiêu có dấu (*) ở B01 (ghi số âm trong ngoặc) và khoản giảm trừ/chi phí là vế trừ trong công thức B02; `is_additive = 1` khi dòng được cộng vào `parent_code`, `0` cho dòng "Trong đó", trống khi chưa chắc (dòng tổng đó không được kiểm). Cha của các chỉ tiêu cấp thấp trong regime TT200 lấy theo bố cục mẫu (I./1./-) vì mẫu TT200/TT202 không in công thức cấp này; cột `source` ghi rõ.

## Mẫu hợp nhất TT99 mà MWG dùng lấy từ đâu

Phụ lục IV TT99 chỉ có mẫu riêng lẻ (B01-DN, B01a-DN...), **không có mẫu `/HN`**. Mẫu `B01a – DN/HN` của MWG (từ BCTC Q1/2026) dùng đúng cấu trúc và mã của TT99 (vd. tổng tài sản 280, bỏ nhóm 410/430, 142 dự phòng hàng tồn kho), cộng thêm các dòng chỉ có ở báo cáo hợp nhất: **B01 429** (lợi ích cổ đông không kiểm soát), **B02 27** (phần lãi/lỗ liên doanh), **61**, **62**. Các dòng này không có trong văn bản nào ở `docs/reference/` nên chưa map và chưa có trong `dim_account` (regime TT99).

## Mã chưa chắc – cần anh/chị xác nhận (62 mục)

### 1. Quan hệ cha–con để trống (23 mã)

| Regime | Báo cáo | Mã | Chỉ tiêu | Lý do |
|---|---|---|---|---|
| TT200 | B01 | 230 | III. Bất động sản đầu tư | công thức in trên mẫu TT200 PL2 '200 = 210 + 220 + 240 + 250 + 260' không có 230, bố cục mẫu thì 230 thuộc mục B |
| TT200 | B02 | 20 | 5. Lợi nhuận gộp về bán hàng và cung cấp dịch vụ (20 = 10 -  | công thức mã 30 trong bản TT202 này '{30 = 20 + (21 - 22) - (24 + 25)}' không khớp cấu trúc (24 là lãi/lỗ liên doanh, 25-26 là chi phí) |
| TT200 | B02 | 21 | 6. Doanh thu hoạt động tài chính | công thức mã 30 trong bản TT202 này '{30 = 20 + (21 - 22) - (24 + 25)}' không khớp cấu trúc (24 là lãi/lỗ liên doanh, 25-26 là chi phí) |
| TT200 | B02 | 22 | 7. Chi phí tài chính | công thức mã 30 trong bản TT202 này '{30 = 20 + (21 - 22) - (24 + 25)}' không khớp cấu trúc (24 là lãi/lỗ liên doanh, 25-26 là chi phí) |
| TT200 | B02 | 24 | 8. Phần lãi hoặc lỗ trong công ty liên doanh, liên kết | công thức mã 30 trong bản TT202 này '{30 = 20 + (21 - 22) - (24 + 25)}' không khớp cấu trúc (24 là lãi/lỗ liên doanh, 25-26 là chi phí) |
| TT200 | B02 | 25 | 9. Chi phí bán hàng | công thức mã 30 trong bản TT202 này '{30 = 20 + (21 - 22) - (24 + 25)}' không khớp cấu trúc (24 là lãi/lỗ liên doanh, 25-26 là chi phí) |
| TT200 | B02 | 26 | 10. Chi phí quản lý doanh nghiệp | công thức mã 30 trong bản TT202 này '{30 = 20 + (21 - 22) - (24 + 25)}' không khớp cấu trúc (24 là lãi/lỗ liên doanh, 25-26 là chi phí) |
| TT99 | B01 | 411 | 1. Vốn góp của chủ sở hữu | mẫu hợp nhất của MWG có thêm 429 (lợi ích cổ đông không kiểm soát) trong vốn chủ sở hữu, không có trong mẫu TT99 riêng lẻ: tổng 400 theo mẫu này sẽ thiếu 429 |
| TT99 | B01 | 412 | 2. Thặng dư vốn | mẫu hợp nhất của MWG có thêm 429 (lợi ích cổ đông không kiểm soát) trong vốn chủ sở hữu, không có trong mẫu TT99 riêng lẻ: tổng 400 theo mẫu này sẽ thiếu 429 |
| TT99 | B01 | 413 | 3. Quyền chọn chuyển đổi trái phiếu | mẫu hợp nhất của MWG có thêm 429 (lợi ích cổ đông không kiểm soát) trong vốn chủ sở hữu, không có trong mẫu TT99 riêng lẻ: tổng 400 theo mẫu này sẽ thiếu 429 |
| TT99 | B01 | 414 | 4. Vốn khác của chủ sở hữu | mẫu hợp nhất của MWG có thêm 429 (lợi ích cổ đông không kiểm soát) trong vốn chủ sở hữu, không có trong mẫu TT99 riêng lẻ: tổng 400 theo mẫu này sẽ thiếu 429 |
| TT99 | B01 | 415 | 5. Cổ phiếu mua lại của chính mình (*) | mẫu hợp nhất của MWG có thêm 429 (lợi ích cổ đông không kiểm soát) trong vốn chủ sở hữu, không có trong mẫu TT99 riêng lẻ: tổng 400 theo mẫu này sẽ thiếu 429 |
| TT99 | B01 | 416 | 6. Chênh lệch đánh giá lại tài sản | mẫu hợp nhất của MWG có thêm 429 (lợi ích cổ đông không kiểm soát) trong vốn chủ sở hữu, không có trong mẫu TT99 riêng lẻ: tổng 400 theo mẫu này sẽ thiếu 429 |
| TT99 | B01 | 417 | 7. Chênh lệch tỷ giá hối đoái | mẫu hợp nhất của MWG có thêm 429 (lợi ích cổ đông không kiểm soát) trong vốn chủ sở hữu, không có trong mẫu TT99 riêng lẻ: tổng 400 theo mẫu này sẽ thiếu 429 |
| TT99 | B01 | 418 | 8. Quỹ đầu tư phát triển | mẫu hợp nhất của MWG có thêm 429 (lợi ích cổ đông không kiểm soát) trong vốn chủ sở hữu, không có trong mẫu TT99 riêng lẻ: tổng 400 theo mẫu này sẽ thiếu 429 |
| TT99 | B01 | 419 | 9. Quỹ khác thuộc vốn chủ sở hữu | mẫu hợp nhất của MWG có thêm 429 (lợi ích cổ đông không kiểm soát) trong vốn chủ sở hữu, không có trong mẫu TT99 riêng lẻ: tổng 400 theo mẫu này sẽ thiếu 429 |
| TT99 | B01 | 420 | 10. Lợi nhuận sau thuế chưa phân phối | mẫu hợp nhất của MWG có thêm 429 (lợi ích cổ đông không kiểm soát) trong vốn chủ sở hữu, không có trong mẫu TT99 riêng lẻ: tổng 400 theo mẫu này sẽ thiếu 429 |
| TT99 | B02 | 20 | 5. Lợi nhuận gộp về bán hàng và cung cấp dịch vụ (20 = 10 -  | mẫu hợp nhất của MWG có thêm 27 (phần lãi/lỗ liên doanh): công thức 30 của mẫu TT99 riêng lẻ không áp dụng được |
| TT99 | B02 | 21 | 6. Lãi/lỗ của hoạt động bán, thanh lý bất động sản đầu tư | mẫu hợp nhất của MWG có thêm 27 (phần lãi/lỗ liên doanh): công thức 30 của mẫu TT99 riêng lẻ không áp dụng được |
| TT99 | B02 | 22 | 7. Doanh thu hoạt động tài chính | mẫu hợp nhất của MWG có thêm 27 (phần lãi/lỗ liên doanh): công thức 30 của mẫu TT99 riêng lẻ không áp dụng được |
| TT99 | B02 | 23 | 8. Chi phí tài chính | mẫu hợp nhất của MWG có thêm 27 (phần lãi/lỗ liên doanh): công thức 30 của mẫu TT99 riêng lẻ không áp dụng được |
| TT99 | B02 | 25 | 9. Chi phí bán hàng | mẫu hợp nhất của MWG có thêm 27 (phần lãi/lỗ liên doanh): công thức 30 của mẫu TT99 riêng lẻ không áp dụng được |
| TT99 | B02 | 26 | 10. Chi phí quản lý doanh nghiệp | mẫu hợp nhất của MWG có thêm 27 (phần lãi/lỗ liên doanh): công thức 30 của mẫu TT99 riêng lẻ không áp dụng được |

### 2. `sign` để trống (2 mã)

| Regime | Báo cáo | Mã | Chỉ tiêu | Lý do |
|---|---|---|---|---|
| TT200 | B02 | 52 | 17. Chi phí thuế TNDN hoãn lại | là vế trừ trong `60 = 50 - 51 - 52` nhưng có thể là thu nhập (số âm); MWG in số âm khi là thu nhập |
| TT99 | B02 | 52 | 17. Chi phí thuế TNDN hoãn lại | là vế trừ trong `60 = 50 - 51 - 52` nhưng có thể là thu nhập (số âm); MWG in số âm khi là thu nhập |

### 3. Mã TT99 chưa map sang mã TT200 (37 mã, trong đó 15 mã MWG đang dùng)

Không có chỉ tiêu **cùng tên** trong mẫu TT200/TT202. Nhiều mã chỉ là đổi tên (vd. "Chi phí chờ phân bổ" ↔ "Chi phí trả trước"), nhưng văn bản không nói hai chỉ tiêu là một nên tôi không tự ghép. Mã **in đậm** là mã MWG có in trên BCTC 2026.

| Báo cáo | Mã TT99 | Chỉ tiêu TT99 | Ghi chú |
|---|---|---|---|
| B01 | **123** | 3. Đầu tư nắm giữ đến ngày đáo hạn ngắn hạn | không có chỉ tiêu cùng tên trong mẫu TT200/TT202: chưa map |
| B01 | 124 | 4. Dự phòng đầu tư nắm giữ đến ngày đáo hạn ngắn hạn (*) | không có chỉ tiêu cùng tên trong mẫu TT200/TT202: chưa map |
| B01 | 125 | 5. Đầu tư ngắn hạn khác | không có chỉ tiêu cùng tên trong mẫu TT200/TT202: chưa map |
| B01 | 126 | 6. Dự phòng tổn thất các khoản đầu tư ngắn hạn khác (*) | không có chỉ tiêu cùng tên trong mẫu TT200/TT202: chưa map |
| B01 | 134 | 4. Phải thu theo tiến độ hợp đồng xây dựng | không có chỉ tiêu cùng tên trong mẫu TT200/TT202: chưa map |
| B01 | 150 | V. Tài sản sinh học ngắn hạn | không có chỉ tiêu cùng tên trong mẫu TT200/TT202: chưa map |
| B01 | 151 | 1. Súc vật nuôi lấy sản phẩm một lần ngắn hạn | không có chỉ tiêu cùng tên trong mẫu TT200/TT202: chưa map |
| B01 | 152 | 2. Cây trồng theo mùa vụ hoặc lấy sản phẩm một lần ngắn hạn | không có chỉ tiêu cùng tên trong mẫu TT200/TT202: chưa map |
| B01 | 153 | 3. Dự phòng tổn thất tài sản sinh học ngắn hạn (*) | không có chỉ tiêu cùng tên trong mẫu TT200/TT202: chưa map |
| B01 | **161** | 1. Chi phí chờ phân bổ ngắn hạn | không có chỉ tiêu cùng tên trong mẫu TT200/TT202: chưa map |
| B01 | 230 | III. Tài sản sinh học dài hạn | không có chỉ tiêu cùng tên trong mẫu TT200/TT202: chưa map |
| B01 | 231 | 1. Súc vật nuôi cho sản phẩm định kỳ | không có chỉ tiêu cùng tên trong mẫu TT200/TT202: chưa map |
| B01 | 232 | a) Súc vật nuôi cho sản phẩm định kỳ chưa đến giai đoạn trưởng thành | không có chỉ tiêu cùng tên trong mẫu TT200/TT202: chưa map |
| B01 | 233 | b) Súc vật nuôi cho sản phẩm định kỳ đến giai đoạn trưởng thành | không có chỉ tiêu cùng tên trong mẫu TT200/TT202: chưa map |
| B01 | 234 | - Nguyên giá | không có chỉ tiêu cùng tên trong mẫu TT200/TT202: chưa map |
| B01 | 235 | - Giá trị khấu hao lũy kế (*) | không có chỉ tiêu cùng tên trong mẫu TT200/TT202: chưa map |
| B01 | 236 | 2. Súc vật nuôi lấy sản phẩm một lần dài hạn | không có chỉ tiêu cùng tên trong mẫu TT200/TT202: chưa map |
| B01 | 237 | 3. Cây trồng theo mùa vụ hoặc lấy sản phẩm một lần dài hạn | không có chỉ tiêu cùng tên trong mẫu TT200/TT202: chưa map |
| B01 | 238 | 4. Dự phòng tổn thất tài sản sinh học dài hạn (*) | không có chỉ tiêu cùng tên trong mẫu TT200/TT202: chưa map |
| B01 | 264 | 4. Dự phòng tổn thất đầu tư vào đơn vị khác dài hạn (*) | không có chỉ tiêu cùng tên trong mẫu TT200/TT202: chưa map |
| B01 | **265** | 5. Đầu tư nắm giữ đến ngày đáo hạn dài hạn | không có chỉ tiêu cùng tên trong mẫu TT200/TT202: chưa map |
| B01 | 266 | 6. Dự phòng đầu tư nắm giữ đến ngày đáo hạn dài hạn (*) | không có chỉ tiêu cùng tên trong mẫu TT200/TT202: chưa map |
| B01 | **271** | 1. Chi phí chờ phân bổ dài hạn | không có chỉ tiêu cùng tên trong mẫu TT200/TT202: chưa map |
| B01 | **313** | 3. Phải trả cổ tức, lợi nhuận | không có chỉ tiêu cùng tên trong mẫu TT200/TT202: chưa map |
| B01 | **314** | 4. Thuế và các khoản phải nộp Nhà nước ngắn hạn | không có chỉ tiêu cùng tên trong mẫu TT200/TT202: chưa map |
| B01 | 318 | 8. Phải trả theo tiến độ hợp đồng xây dựng ngắn hạn | không có chỉ tiêu cùng tên trong mẫu TT200/TT202: chưa map |
| B01 | **319** | 9. Doanh thu chờ phân bổ ngắn hạn | không có chỉ tiêu cùng tên trong mẫu TT200/TT202: chưa map |
| B01 | 333 | 3. Thuế và các khoản phải nộp Nhà nước dài hạn | không có chỉ tiêu cùng tên trong mẫu TT200/TT202: chưa map |
| B01 | **337** | 7. Doanh thu chờ phân bổ dài hạn | không có chỉ tiêu cùng tên trong mẫu TT200/TT202: chưa map |
| B01 | **412** | 2. Thặng dư vốn | không có chỉ tiêu cùng tên trong mẫu TT200/TT202: chưa map |
| B01 | **415** | 5. Cổ phiếu mua lại của chính mình (*) | không có chỉ tiêu cùng tên trong mẫu TT200/TT202: chưa map |
| B02 | 21 | 6. Lãi/lỗ của hoạt động bán, thanh lý bất động sản đầu tư | không có chỉ tiêu cùng tên trong mẫu TT200/TT202: chưa map |
| B02 | **24** | - Trong đó: Chi phí đi vay | không có chỉ tiêu cùng tên trong mẫu TT200/TT202: chưa map |
| B01 | **429** | (không có trong mẫu TT99) | MWG in mã này trên mẫu B01a/B02a-DN/HN (Lợi ích cổ đông không kiểm soát) nhưng mẫu TT99 PL IV không có: chưa map |
| B02 | **27** | (không có trong mẫu TT99) | MWG in mã này trên mẫu B01a/B02a-DN/HN (Phần lãi/lỗ trong công ty liên doanh) nhưng mẫu TT99 PL IV không có: chưa map |
| B02 | **61** | (không có trong mẫu TT99) | MWG in mã này trên mẫu B01a/B02a-DN/HN (Lợi nhuận sau thuế của công ty mẹ) nhưng mẫu TT99 PL IV không có: chưa map |
| B02 | **62** | (không có trong mẫu TT99) | MWG in mã này trên mẫu B01a/B02a-DN/HN (Lợi nhuận sau thuế của cổ đông không kiểm soát) nhưng mẫu TT99 PL IV không có: chưa map |

<!-- numeric-map:start -->

## Map bằng số liệu ngày 31/12/2025 (tự sinh bởi `tools/map_tt99_numeric.py`)

So cột đầu năm B01 của BCTC 2026 (TT99) với cột cuối kỳ B01 của BCTC 2025 bản vi-VN (TT200), chỉ giữa các mã cùng cấp. Khớp đúng một mã thì ghép (`mapping_basis = numeric_31122025`). `semantic_break`: mã ghép theo tên mà số lệch, không giống lỗi OCR (cùng tên, khác nội dung).

| Kết quả | Số mã | Mã TT99 → mã TT200 khớp số |
|---|---|---|
| mới ghép | 6 | 161 → 151, 265 → 255, 271 → 261, 314 → 313, 412 → 412, 415 → 415 |
| khớp với ghép theo tên | 41 | 100 → 100, 111 → 111, 131 → 131, 132 → 132, 140 → 140, 141 → 141, 142 → 149, 160 → 150, 162 → 152, 163 → 153, 200 → 200, 210 → 210, 215 → 216, 220 → 220, 221 → 221, 222 → 222, 223 → 223, 227 → 227, 228 → 228, 229 → 229, 250 → 240, 252 → 242, 260 → 250, 262 → 252, 270 → 260, 272 → 262, 300 → 300, 311 → 311, 312 → 312, 315 → 314, 316 → 315, 320 → 319, 321 → 320, 322 → 321, 330 → 330, 400 → 400, 411 → 411, 411a → 411a, 420 → 421, 420a → 421a, 420b → 421b |
| mâu thuẫn với ghép theo tên | 0 |  |
| khớp nhiều mã | 2 | 280 → 270/440, 440 → 270/440 |
| không khớp | 10 | 110 → –, 112 → –, 120 → –, 123 → –, 130 → –, 135 → –, 310 → –, 319 → –, 337 → –, 429 → – |
| bằng 0 | 0 |  |
| semantic_break | 2 | 120 → 120, 130 → 130 |
| báo cáo 2026 không thống nhất | 7 | 110 → 4,999,925,717,187/5,000,160,018,899, 112 → 39,550,000,000/39,784,301,712, 120 → 41,883,889,962,478/41,884,124,264,190, 123 → 41,883,889,962,478/41,884,124,264,190, 310 → 50,380,613,547,353/50,769,502,237,457, 319 → 148,048,028,340/536,936,718,444, 322 → 89,498,061,130/899,498,061,130 |

<!-- numeric-map:end -->

<!-- probe-30:start -->

## Dò công thức mã 30 (B02 TT200) bằng số liệu (tự sinh bởi `tools/probe_b02_formula.py`)

Chạy trên các báo cáo TT200 đã qua critical B01; dòng 24 không in tính là 0; báo cáo thiếu vế khác (OCR mất mã) không tính. Chọn công thức đúng ở > 90% báo cáo kiểm được.

| Công thức 30 = | Nguồn ứng viên | Đúng / kiểm được | Tỷ lệ | Báo cáo trượt |
|---|---|---|---|---|
| 20+21-22+24-25-26 | cấu trúc mẫu B02-DN/HN: 30 = 20 + (21 - 22) + 24 - (25 + 26) | 36/38 | 95% | 2022-Q3 vi, 2023-Q4 vi |
| 20+21-22-24-25 | công thức in trong bản TT202 của TVT-ProTrain: 30 = 20 + (21 - 22) - (24 + 25) | 0/38 | 0% | 2016 en, 2016-H1 en, 2016-Q1 en, 2016-Q3 en, 2017 en, 2017-Q3 en, 2018 en, 2018-H1 en |
| 20+21-22-25-26 | mẫu B02-DN riêng lẻ (không có mã 24): 30 = 20 + (21 - 22) - (25 + 26) | 9/38 | 24% | 2018 en, 2018-H1 en, 2018-Q3 en, 2019 en, 2019-H1 en, 2019-Q3 en, 2020 en, 2020-H1 en |

**Chọn:** `30 = 20+21-22+24-25-26` (36/38).

<!-- probe-30:end -->
