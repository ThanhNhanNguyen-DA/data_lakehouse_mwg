# Nhật ký lỗi và cách xử lý (phụ lục đồ án)

Nguồn bằng chứng: `git log`, `docs/dq_report.md` (kể cả các phiên bản cũ trong git), `docs/limitations.md`,
`CLAUDE.md`, code và test hiện có. Mục nào không tìm được bằng chứng (commit, test, số liệu) ghi **chưa xác minh**.
Số liệu "trước → sau" lấy từ mục tự sinh của `docs/dq_report.md` ở các commit tương ứng.

**Quy ước:** từ nay mỗi lần sửa lỗi đáng kể thì thêm một mục DBG mới vào file này **trong cùng commit** với bản sửa.
Mục do commit hiện tại thêm vào ghi `Commit: commit thêm mục này` (tra bằng `git log -- docs/debug_log.md`).

---

## Crawl và Bronze

### DBG-01 – robots.txt của bhxcdn.vn trả 403
- Giai đoạn / tầng: crawl
- Triệu chứng: file PDF/ảnh nằm trên `bhxcdn.vn`; robots.txt của host này trả HTTP 403. Bản đầu của `http_utils`
  coi robots.txt lỗi là cấm tất cả (test cũ `test_robots_txt_403_thi_khong_tai_gi`), nên không tải được file nào.
- Nguyên nhân gốc: hiểu sai quy ước robots.txt. RFC 9309 mục 2.3.1: robots.txt trả 4xx (kể cả 401/403) nghĩa là
  "không có quy tắc"; chỉ 5xx/lỗi mạng mới phải coi là cấm toàn bộ.
- Cách phát hiện: chạy crawler thật sau khi khảo sát API tuần 1.
- Cách xử lý: `src/ingestion/http_utils.py` (comment dẫn RFC 9309 mục 2.3.1): robots.txt 4xx = không giới hạn,
  5xx = dừng. 403 trên **tài liệu** vẫn dừng cả lần chạy và ghi log, không tìm cách vượt.
- Kiểm chứng: `tests/test_http_utils.py::test_robots_txt_4xx_la_khong_gioi_han`, `test_robots_txt_5xx_thi_dung`,
  `test_403_dung_ngay_khong_retry`; `tests/test_crawler.py::test_403_van_dung_ca_lan_chay`.
- Commit: `8b33a60` (mô tả commit: "robots.txt trả 4xx = không giới hạn theo RFC 9309 (bhxcdn.vn trả 403)").
- Bài học / hạn chế còn lại: đọc đặc tả trước khi chọn hành vi "an toàn"; quá chặt cũng là lỗi.

### DBG-02 – Link chết trên host cũ `mwg.vn/wp-content` → nguồn dự phòng en-US
- Giai đoạn / tầng: crawl
- Triệu chứng: API vẫn liệt kê tài liệu cũ nhưng link trỏ về `mwg.vn/wp-content/uploads/...` và trả 404. Sau backfill:
  72 link chết (69 vi-VN, 3 en-US); bản vi-VN mất 56 tài liệu + 13 ảnh báo cáo tháng (`docs/limitations.md` mục 1).
- Nguyên nhân gốc: mwg.vn chuyển từ WordPress sang nền tảng mới, không chuyển file cũ.
- Cách phát hiện: backfill dừng giữa chừng vì 404 (xem DBG-03); đếm `download_errors.csv`.
- Cách xử lý: thêm nguồn **en-US do chính MWG công bố** (danh mục gốc 61, link `bhxcdn.vn` còn sống), chỉ tải khi kỳ
  chưa có bản vi-VN (`fetch_reason = fallback`) hoặc nằm trong `cross_check`; Silver ưu tiên vi-VN. Không dùng
  Wayback Machine. `config/sources.yaml > listing.languages`, `src/ingestion/mwg_crawler.py`.
- Kiểm chứng: `tests/test_crawler.py::test_ban_chinh_chet_link_thi_tai_ban_du_phong`,
  `test_crawl_tai_ban_chinh_va_du_phong_khi_thieu`, `test_cross_check_tai_ca_hai_ngon_ngu`; manifest 161 file
  (101 vi-VN, 60 en-US); báo cáo tháng lấp được 41/44 kỳ bằng en-US.
- Commit: `d441be5` (BCTC en-US), `64de1b2` (báo cáo tháng en-US), `99c69f2` (limitations.md).
- Bài học / hạn chế còn lại: vẫn thiếu FY2014, H1/2014, Q1/2019, tháng 01–02/2019, 05/2017 (`docs/limitations.md`
  mục 3–4); một phần lấp bằng số so sánh ở Silver.

### DBG-03 – Backfill dừng hẳn khi gặp một link 404
- Giai đoạn / tầng: crawl
- Triệu chứng: một PDF cũ trên mwg.vn trả 404 làm dừng cả lần backfill.
- Nguyên nhân gốc: mọi lỗi HTTP đều dừng chương trình (đúng với 403, sai với link chết).
- Cách phát hiện: traceback khi chạy backfill (người dùng dán log).
- Cách xử lý: 404/410 ghi vào `data/_state/download_errors.csv` rồi tải tiếp, không ghi vào manifest để lần sau thử
  lại; 403 và lỗi khác vẫn dừng.
- Kiểm chứng: `tests/test_crawler.py::test_link_chet_ghi_lai_va_tai_tiep`, `test_403_van_dung_ca_lan_chay`,
  `test_loi_http_khac_van_dung`.
- Commit: `521cf59`.
- Bài học / hạn chế còn lại: phân biệt lỗi "bỏ qua được" và lỗi "phải dừng" ngay từ đầu.

### DBG-04 – Ảnh JPG/PNG báo cáo tháng bị bỏ qua
- Giai đoạn / tầng: crawl / Bronze
- Triệu chứng: báo cáo tóm tắt KQKD tháng 2017 – 01/2018 là ảnh `.jpg`, crawler bỏ qua vì chỉ nhận nội dung bắt đầu
  bằng `%PDF`.
- Nguyên nhân gốc: giả định mọi tài liệu là PDF.
- Cách phát hiện: log "Bỏ qua file không phải PDF" khi backfill; người dùng hỏi lại.
- Cách xử lý: Bronze giữ cả ảnh; `file_type` theo magic bytes (không theo đuôi URL); ảnh đi đường OCR ở bước bóc tách.
- Kiểm chứng: `tests/test_crawler.py::test_file_type_theo_noi_dung_khong_theo_duoi_url`, `test_detect_file_type`,
  `test_noi_dung_khong_phai_pdf_hay_anh_thi_bo_qua`; 11 file `.jpg` năm 2017 trong manifest.
- Commit: `8143472`.
- Bài học / hạn chế còn lại: tầng thô nên giữ mọi thứ nguồn công bố; lọc ở tầng sau.

### DBG-05 – MWG đặt link sai (1H17 → file Q3/2017; YTD May 2017 → file 05/2018)
- Giai đoạn / tầng: crawl / Bronze
- Triệu chứng: hai mục khác kỳ trỏ cùng một URL: "Interim Audited Consolidate FS 1H17" và "3Q17" cùng
  `IC-FS-30.09.2017-consolE2.pdf`; "YTD May 2017 report" và "YTD May 2018 report" cùng `Monthly-report-YTD-May-18-EN.pdf`.
- Nguyên nhân gốc: lỗi dữ liệu phía nguồn.
- Cách phát hiện: manifest khóa theo URL nên mục thứ hai bị coi là "đã tải"; đối chiếu tiêu đề với kỳ trong manifest.
- Cách xử lý: ghi `data/_state/link_anomalies.csv` (4 dòng); crawler gom **toàn bộ listing** rồi nhóm theo URL: URL
  nhiều kỳ → tải một lần với `period = pending`, `period_ambiguous = true`, `period_candidates`; kỳ thật gán sau khi
  đọc ngày kết thúc kỳ in trong PDF (`mwg_crawler.resolve_period()`). Bronze không sửa.
- Kiểm chứng: `tests/test_crawler.py::test_url_dung_chung_giua_hai_ky_tai_mot_lan_voi_pending`,
  `test_file_da_luu_bi_danh_dau_pending_roi_gan_ky_that`, `test_hai_ky_khac_loai_tai_lieu_thi_doc_type_pending`;
  `tests/test_extraction.py::test_check_period_pending_chon_dung_ung_vien`. Manifest hiện tại: file Q3/2017 → `2017-Q3`,
  file tháng 5 → `2018-M05` (đều `period_ambiguous = true`), tức kỳ đọc từ PDF khớp tên file.
- Commit: `755a466`, `80a0c4e` (gom theo URL), `b350625` (đối chiếu ngày trong PDF).
- Bài học / hạn chế còn lại: H1/2017 và 05/2017 không có tài liệu gốc ở cả hai ngôn ngữ. Bảng mục 2 trong
  `docs/dq_report.md` vẫn ghi "chờ" ở cột "Kỳ sau khi đọc PDF" (chưa cập nhật tay).

### DBG-06 – Tiêu đề tài liệu khó parse: QIII, thiếu năm, "cả năm", "mùa Tết"
- Giai đoạn / tầng: crawl (nhận diện loại tài liệu và kỳ)
- Triệu chứng: tiêu đề thật dùng số La Mã ("Quý III", "QIII_2016"), không ghi năm, "cả năm", "Báo cáo tóm tắt KQKD
  mùa Tết (tháng 1+2) năm 2022", tiếng Anh "YTD June 2017report", "3Q19", "1H2020".
- Nguyên nhân gốc: tiêu đề do người nhập tay, không theo mẫu.
- Cách phát hiện: hiệu chỉnh `doc_rules` trên 292 tiêu đề vi-VN thật (2014–2026) và toàn bộ tiêu đề en-US thật.
- Cách xử lý: `classify`/`parse_period` theo `doc_rules` có thứ tự, khớp nguyên từ, lấy năm của danh mục khi tiêu đề
  không ghi năm; "tháng 1+2" → `M02`; tên tháng tiếng Anh.
- Kiểm chứng: `tests/test_doc_rules.py` (`test_tieu_de_that`, `test_tieu_de_that_khong_ghi_nam`,
  `test_parse_period_truong_hop_bien`, `test_tieu_de_en_that`, `test_bao_cao_thang_en_that` – 142 tiêu đề en thật).
- Commit: `8b33a60`, `d441be5`, `64de1b2`.
- Bài học / hạn chế còn lại: tiêu đề mới có dạng lạ sẽ rơi vào "không lấy"; cần chạy `--dry-run` sau mỗi kỳ công bố.

## OCR và bóc tách

### DBG-07 – BCTC là bản scan → OCR là đường chính
- Giai đoạn / tầng: OCR
- Triệu chứng: cả 19 BCTC en-US (2016–2020, Q2/2022) và BCTC Q3/2026 vi-VN là bản scan (`has_text_layer = false`);
  `pdfplumber` không lấy được chữ.
- Nguyên nhân gốc: MWG đăng bản scan có chữ ký/con dấu.
- Cách phát hiện: `pdf_extractor.has_text_layer()` khi crawl (`docs/limitations.md` mục 5).
- Cách xử lý: chọn đường bóc theo `has_text_layer`; bản scan: Tesseract `vie+eng` qua `pytesseract`, render bằng
  `pypdfium2`, hai lượt (100 dpi tìm trang, 300 dpi TSV), cache theo sha256 + trang + dpi (`src/extraction/ocr.py`).
- Kiểm chứng: `tests/test_pdf_numbers.py::test_has_text_layer`, `tests/test_crawler.py::test_ghi_has_text_layer`,
  `tests/test_extraction.py::test_ocr_text_xoay_theo_osd_va_cache`; `tests/test_fixtures_ocr.py` (2 PDF mẫu, cần
  Tesseract; cột số kỳ vọng còn để trống chờ người dùng điền).
- Commit: `d441be5` (has_text_layer), `b350625` (OCR), `8690c2e` (lượt OCR đầu: 52 BCTC + 109 báo cáo tháng).
- Bài học / hạn chế còn lại: thực tế mọi BCTC trong lượt chạy đều đi đường OCR; độ chính xác OCR là nút thắt của
  cả đồ án (xem DBG-11, DBG-12, DBG-13).

### DBG-08 – Trang B02 in xoay ngang, mỗi trang một chiều
- Giai đoạn / tầng: OCR
- Triệu chứng: trang B02 quý in khổ ngang; trong cùng một file có trang xoay 90° và trang xoay ngược chiều.
- Nguyên nhân gốc: bản scan giữ hướng giấy khi in.
- Cách phát hiện: chưa xác minh (không còn log lượt chạy trước khi thêm xoay trang).
- Cách xử lý: OSD của Tesseract ở 150 dpi cho **từng trang**; độ tin cậy thấp thì không xoay
  (`ocr.detect_rotation`, `ocr.page_rotation`).
- Kiểm chứng: `tests/test_extraction.py::test_ocr_text_xoay_theo_osd_va_cache`, `test_osd_tin_cay_thap_thi_khong_xoay`.
- Commit: `8690c2e` ("xoay trang theo OSD của Tesseract").
- Bài học / hạn chế còn lại: chỉ số "trước → sau" chưa xác minh.

### DBG-09 – Bố cục B02 quý khác BCTC năm (mã sau tên, "1" thay "01", cột STT)
- Giai đoạn / tầng: bóc tách
- Triệu chứng: B02 quý đặt cột mã ở giữa, có cột số thứ tự bên trái, in mã "1", "2" thay vì "01", "02"; bản en dùng
  dấu phẩy và gạch ngang.
- Nguyên nhân gốc: MWG dùng mẫu trình bày khác nhau giữa báo cáo quý và năm, giữa vi và en.
- Cách phát hiện: dựng bảng sai cột trên các fixture bố cục.
- Cách xử lý: neo theo cột tiêu đề "Mã số"/"Code" thay vì theo vị trí/tên chỉ tiêu; `code.zfill(2)` cho mã một chữ số
  (`pdf_extractor.py`, comment "BCTC quý in "1", "2""); nối tên chỉ tiêu xuống dòng; trang sau không có tiêu đề cột
  dùng lại cột trang trước.
- Kiểm chứng: `tests/test_extraction.py::test_b02_quy_ma_o_giua_so_thu_tu_ben_trai_bon_cot`,
  `test_bo_cuc_nam_vi_ma_ben_trai_ten_xuong_dong`, `test_bo_cuc_en_dau_phay_va_gach_ngang`,
  `test_trang_tiep_theo_khong_co_tieu_de_cot_dung_lai_trang_truoc`.
- Commit: `b350625`.
- Bài học / hạn chế còn lại: không gán cột theo thứ tự; TT99 đổi thứ tự cột B02 (DBG-15).

### DBG-10 – Khoản giảm trừ in số âm trong ngoặc → sign lấy từ dim_account
- Giai đoạn / tầng: DQ
- Triệu chứng: lượt OCR đầu, `10 = 01 - 02` trượt ở mọi báo cáo TT200 với độ lệch đúng bằng 2 lần mã 02 (vd. 2016 en:
  lệch -1,998,688,258,056); BCTC TT200 của MWG in 02/11/22/25/26 âm trong ngoặc, TT99 in dương.
- Nguyên nhân gốc: phương trình trừ một số đã âm; `dim_account` lúc đó có 0 dòng nên chưa có `sign`.
- Cách phát hiện: bảng chi tiết theo kỳ của `docs/dq_report.md` ở commit `8690c2e`.
- Cách xử lý: trị tuyệt đối **chỉ** cho mã có `sign = -1` trong `config/dim_account.csv`
  (`pdf_extractor._contribution`); phương trình có vế trừ chưa có sign thì không đánh giá (`eligible_equations`);
  dim_account dựng từ văn bản gốc trong `docs/reference/` (`tools/build_dim_account.py`).
- Kiểm chứng: `tests/test_extraction.py::test_tri_tuyet_doi_chi_cho_ma_sign_am`,
  `tests/test_self_correct.py::test_phuong_trinh_danh_gia_duoc`, `test_goi_y_giu_dau_in_cho_khoan_giam_tru`.
- Commit: `8690c2e` (cơ chế), `1438645` (dim_account 287 dòng).
- Bài học / hạn chế còn lại: TT200 mã 52 (thuế hoãn lại) chưa có sign nên `60 = 50 - 51 - 52` chưa kiểm được;
  thay bằng `60 = 61 + 62` (DBG-22).

### DBG-11 – Đường kẻ bảng làm OCR mất mã/số (vd. mã 270)
- Giai đoạn / tầng: OCR
- Triệu chứng: lượt OCR đầu còn "thiếu mã 270" ở 2016 en, 2020-H1 en, 2021-Q1 vi (bảng chi tiết ở commit `8690c2e`).
- Nguyên nhân gốc: đường kẻ dọc/ngang sát chữ số làm Tesseract đọc dính hoặc bỏ cả ô.
- Cách phát hiện: xem ảnh crop các ô thiếu.
- Cách xử lý: `ocr.clean_rules()` xóa đường kẻ (dò dải điểm ảnh liên tục dài) trước khi OCR **từng cột** số tiền
  (whitelist `0123456789.,()-`) và cột mã (whitelist `0123456789ab`).
- Kiểm chứng: `tests/test_extraction.py::test_thay_so_tien_bang_ocr_tung_cot`; `clean_rules` chưa có test riêng.
  Chỉ số trước → sau: chưa xác minh (xóa đường kẻ và OCR từng cột vào cùng commit với lượt OCR đầu).
- Commit: `8690c2e`.
- Bài học / hạn chế còn lại: mã in đậm vẫn hay mất (DBG-12).

### DBG-12 – OCR mất mã dòng in đậm (10, 20, 270, 440); 440 đọc ra rác
- Giai đoạn / tầng: OCR / DQ
- Triệu chứng: dòng tổng in đậm có số tiền nhưng không có mã; ô 440 (đậm, gạch chân kép) thường đọc ra 1–2 chữ số
  (vd. 2016-H1 en: 440 = 1 trong khi 300 + 400 = 7.994 tỷ). `440 = Σ con` trượt 34/48 báo cáo TT200 (commit `399ed86`).
- Nguyên nhân gốc: nét đậm và gạch chân dính vào chữ số ở 300 dpi.
- Cách phát hiện: thống kê "dòng có số mà thiếu mã" và bảng "Phương trình cha–con: số báo cáo trượt".
- Cách xử lý: (1) OCR lại dải mã của dòng thiếu mã ở 400 dpi, làm mảnh nét, `--psm 7`; ô số sai mẫu nhóm 3 chữ số
  hoặc chỉ 1–2 chữ số → OCR lại riêng ô (`run_extraction._reocr_fixes`, `ocr.ocr_region`); (2) **440 := 270**
  (`config/dq_identities.csv`, TT99: 440 := 280) với `value_origin = identity`, giữ số OCR ở `ocr_value`; bỏ phép kiểm
  `440 = Σ con`; ô 440 trusted khi ô 270 trusted (`run_extraction.apply_identities`).
- Kiểm chứng: `tests/test_self_correct.py::test_parse_tra_dong_thieu_ma_va_o_sai_mau`,
  `test_reocr_fixes_them_ma_va_sua_o`, `test_dong_nhat_thuc_440_bang_270`, `test_bo_kiem_tong_dong_con_cua_dong_dich`;
  `tests/test_extraction.py::test_o_mot_hai_chu_so_la_dang_ngo`. Commit `399ed86`: 141 mã và 108 ô được OCR lại.
  Sau đồng nhất thức: 86 ô `identity`; `440 = Σ con` không còn trong top 10; dòng "2018 mã 440" (lệch 180.130 đồng)
  không còn trong bảng trình bày lại.
- Commit: `161cd7a` (OCR lại), `1438645` (ô 1–2 chữ số), commit thêm mục này (đồng nhất thức).
- Bài học / hạn chế còn lại: đồng nhất thức chỉ dùng khi văn bản gốc khẳng định hai dòng bằng nhau; ô 440 không còn
  là bằng chứng độc lập.

### DBG-13 – Sai chữ số → bỏ phiếu đa số, kiểm mẫu nhóm 3 chữ số, PaddleOCR (lỗi oneDNN)
- Giai đoạn / tầng: OCR / tự sửa
- Triệu chứng: B01 cân đối chỉ 32/52 (62%) ở lượt OCR đầu (đếm từ bảng chi tiết commit `8690c2e`); độ chính xác OCR
  thô B01 34/52 (65%), 82/108 (76%) phép kiểm phương trình B01 đúng (commit `1438645`).
- Nguyên nhân gốc: Tesseract nhầm 1/4/7, 3/8, 0/6 trên bản scan.
- Cách phát hiện: phương trình kế toán trượt với độ lệch có dạng một chữ số.
- Cách xử lý: (1) bỏ phiếu đa số giữa các lần một số B01 31/12/N được in lại (cuối kỳ BCTC năm/Q4 N, đầu năm các báo
  cáo N+1), chỉ sửa khác biệt giống lỗi OCR (`self_correct.ocr_like`, `apply_consensus`); (2) ô sai mẫu nhóm 3 chữ số
  → OCR lại ô; (3) trang còn sai phương trình → PaddleOCR mobile trên CPU, giữ nếu ít lỗi hơn
  (`run_extraction._paddle_fallback`). PaddleOCR 3.x lỗi oneDNN trên CPU → `enable_mkldnn=False`; mô hình medium quá
  chậm → mobile (thời gian mỗi trang: chưa xác minh, không lưu log); (4) gợi ý sửa một ô theo phương trình
  → `manual_overrides.csv`, `status = pending`, chỉ áp dụng khi người dùng đổi thành `confirmed`.
- Kiểm chứng: `tests/test_self_correct.py::test_ocr_like`, `test_bo_phieu_sua_o_loi_ocr_va_dien_o_thieu`,
  `test_bo_phieu_khong_ghi_de_so_trinh_bay_lai`, `test_bo_phieu_hoa_thi_khong_sua`, `test_goi_y_sua_mot_o_theo_phuong_trinh`,
  `test_ghi_goi_y_pending_va_khong_ap_dung`. B01 cân đối 32/52 → 49/52 (commit `161cd7a`); commit `399ed86`: 272 ô
  sửa/điền theo bỏ phiếu, PaddleOCR chạy 42 trang, thay kết quả cho 17 báo cáo.
- Commit: `161cd7a`.
- Bài học / hạn chế còn lại: tự sửa chỉ khi có bằng chứng độc lập; ô không có bản in lại thì phải dựa vào người dùng
  xác nhận (trang `review/overrides_review.html`).

### DBG-14 – OCR đọc sai năm ở báo cáo tháng 04/2023 và 08/2023
- Giai đoạn / tầng: bóc tách (đối chiếu kỳ)
- Triệu chứng: báo cáo tháng 2023-M04 và 2023-M08 đọc ra kỳ 2025-M04, 2025-M08 → lệch kỳ manifest.
- Nguyên nhân gốc: OCR nhầm chữ số năm (3 → 5).
- Cách phát hiện: bảng "Báo cáo tháng – kỳ đọc trong file khớp manifest" trong `docs/dq_report.md`.
- Cách xử lý: ngày đọc được phải **trước `publishDate`** và không quá 125 ngày (`run_extraction.plausible_end`,
  `MAX_PUBLISH_LAG_DAYS`); vi phạm → không tin ngày đó, giữ kỳ theo tiêu đề, gắn `ocr_date_suspect`.
- Kiểm chứng: `tests/test_self_correct.py::test_ngay_doc_duoc_hop_ly`, `test_ngay_kho_tin_giu_ky_theo_tieu_de`;
  `docs/dq_report.md`: 2023-M04, 2023-M08 ghi "ocr_date_suspect: giữ kỳ theo tiêu đề"; báo cáo tháng đúng kỳ 102/109.
- Commit: `161cd7a`.
- Bài học / hạn chế còn lại: ngày suy từ metadata nguồn là ràng buộc rẻ và mạnh cho OCR.

### DBG-15 – Tiêu đề cột B02 TT99 không đọc được → gắn nhãn cột bằng số liệu
- Giai đoạn / tầng: bóc tách
- Triệu chứng: tiêu đề cột B02 TT99 in nghiêng cỡ nhỏ, OCR không đọc được; TT99 đổi thứ tự cột nên không được gán
  theo vị trí.
- Nguyên nhân gốc: chữ nhỏ trên bản scan; mẫu mới.
- Cách phát hiện: `columns_ok = false` cho các báo cáo TT99 (commit `161cd7a`).
- Cách xử lý: OCR lại dải tiêu đề 500 dpi; không được thì chứng minh bằng số: hai cột năm trước khớp báo cáo cùng kỳ
  năm trước (≥ 3 mã chuẩn), hai cột năm nay phân biệt bằng lũy kế − quý này = lũy kế kỳ trước
  (`run_extraction.relabel_b02_numeric`, `b02_label_method`).
- Kiểm chứng: `tests/test_self_correct.py::test_gan_nhan_cot_b02_bang_so_q3`, `test_gan_nhan_cot_b02_bang_so_h1_va_q1`,
  `test_gan_nhan_cot_b02_bang_so_that_bai_thi_giu_co`, `test_cot_b02_tt99_khong_doc_duoc_tieu_de_thi_gan_co`;
  `b02_label_method`: header 7, numeric 3.
- Commit: `1438645`.
- Bài học / hạn chế còn lại: Q2/2026 chưa chứng minh được → không vào Gold.

### DBG-16 – 3 báo cáo không nhận diện được regime
- Giai đoạn / tầng: bóc tách
- Triệu chứng: lượt OCR đầu có 3 báo cáo regime "không nhận diện" (vd. 2021 vi: không kiểm phương trình nào).
- Nguyên nhân gốc: OCR không đọc được tên B01 trên trang.
- Cách phát hiện: bảng theo regime của `docs/dq_report.md` (commit `8690c2e`: "không nhận diện 0/3").
- Cách xử lý: năm < 2026 (năm đầu TT99) → coi là TT200, gắn `regime_inferred` (`run_extraction._make_doc`).
- Kiểm chứng: `tests/test_self_correct.py::test_regime_suy_ra_theo_nam`; hiện "TT200 (suy ra)" 3/3 B01 cân đối.
- Commit: `161cd7a`.
- Bài học / hạn chế còn lại: từ 2026 không suy được (cả hai regime đều có thể) → để trống.

### DBG-17 – Bản TT202 bên thứ ba có công thức mã 30 nghi sai; dò công thức bằng số liệu
- Giai đoạn / tầng: DQ / dim_account
- Triệu chứng: bản TT202 do TVT-ProTrain soạn lại ghi `30 = 20 + (21 - 22) - (24 + 25)`, không khớp cấu trúc (24 là
  lãi/lỗ liên doanh, 25–26 là chi phí); TT200 B02 30 nằm trong "critical chưa kiểm được".
- Nguyên nhân gốc: văn bản tham chiếu không phải bản chính thức.
- Cách phát hiện: `tools/build_dim_account.py` đánh dấu "KHÔNG CHẮC" (`docs/dim_account_notes.md`).
- Cách xử lý: `tools/probe_b02_formula.py` chạy 3 công thức ứng viên trên báo cáo TT200 đã qua critical B01, chọn
  công thức đúng > 90%: `30 = 20 + (21 - 22) + 24 - (25 + 26)` đúng 36/38 (95%), bản TVT-ProTrain 0/38, bản không có
  24 là 9/38. Đưa vào `config/dq_equations.csv` (critical), bằng chứng ở `docs/dim_account_notes.md`.
- Kiểm chứng: số liệu trên (bảng tự sinh trong `docs/dim_account_notes.md`).
- Commit: commit thêm mục này.
- Bài học / hạn chế còn lại: khi có bản TT202 chính thức trong `docs/reference/` thì công thức in trong đó thay kết quả
  dò; dò bằng số chỉ chứng minh công thức khớp dữ liệu MWG, không thay văn bản pháp lý.

### DBG-18 – Phương trình mã 30 trượt hàng loạt vì MWG bỏ dòng 24
- Giai đoạn / tầng: DQ
- Triệu chứng: sau khi thêm `30 = ...` làm critical, B02 qua critical giảm 27/52 → 11/52; phần lớn ghi chú "thiếu 24"
  (34 lần) (output `run_extraction` trong phiên 12/11, `dq_report.md` trung gian không lưu).
- Nguyên nhân gốc: 16/47 báo cáo TT200 không in dòng 24 (2016–2018 en, 2023 vi), mẫu cho phép bỏ dòng không có số liệu.
- Cách phát hiện: đếm lý do trượt; đếm báo cáo có/không có dòng 24 trong `fs_extracted_raw`.
- Cách xử lý: cột `optional` trong `config/dq_equations.csv`: mã được phép không in thì tính là 0 (chỉ mã 24 của
  phương trình 30); vế khác vẫn báo thiếu.
- Kiểm chứng: `tests/test_extraction.py::test_ma_tuy_chon_khong_in_tinh_la_0`; B02 qua critical 11/52 → 25/52; phương trình 30 còn trượt 7/48 (6 vì OCR mất mã 22/25/30, 1 lệch số).
- Commit: commit thêm mục này.
- Bài học / hạn chế còn lại: nếu OCR làm mất mã 24 mà số thật khác 0 thì phương trình trượt (đúng hành vi).

## Map TT99 và dim_account

### DBG-19 – Chuyển TT200 → TT99 năm 2026: đổi tên B01, tổng tài sản 280, mã dịch chuyển
- Giai đoạn / tầng: map TT99
- Triệu chứng: từ BCTC 2026, B01 tên "Báo cáo tình hình tài chính", tổng tài sản mã 280 (TT200: 270), nhiều mã B01/B02
  dịch chuyển; phương trình TT200 không áp dụng được.
- Nguyên nhân gốc: Thông tư 99/2025/TT-BTC.
- Cách phát hiện: lượt OCR đầu: 4 báo cáo TT99 trượt mọi phương trình TT200.
- Cách xử lý: nhận diện regime theo tên B01 (`pdf_extractor.detect_regime`); phương trình theo regime trong
  `config/dq_equations.csv`; `config/account_code_regime_map.csv` ghép theo tên trùng khớp (`tools/build_dim_account.py`),
  rồi ghép bằng số liệu 31/12/2025 (`tools/map_tt99_numeric.py`): khớp duy nhất → `mapping_basis = numeric_31122025`.
  Mốc so sánh đổi từ Q4/2025 sang BCTC năm 2025 đã kiểm toán (`--ref 2025`): khớp tên 28 → 41, ghép mới 5 → 6, mâu
  thuẫn 1 → 0, không khớp 26 → 10. Mỗi lần chạy tính lại phần do số liệu sinh ra (trước đây chạy lại sẽ coi ghép
  numeric cũ là ghép theo tên).
- Kiểm chứng: `tests/test_extraction.py::test_detect_regime_theo_ten_b01`, `test_ma_chuan_va_doi_chieu_dau_nam_qua_regime`,
  `test_check_equations_chi_dung_phuong_trinh_cua_regime`, `test_cot_tt99_quy_iii_va_luy_ke`; bảng tự sinh trong
  `docs/dim_account_notes.md`; TT99 B01 cân đối 4/4.
- Commit: `8690c2e` (regime), `1438645` (dim + map theo tên), `399ed86` (map bằng số), commit thêm mục này (`--ref 2025`).
- Bài học / hạn chế còn lại: 10 mã TT99 chưa khớp số; 8 mã mà các báo cáo 2026 in số 31/12/2025 không thống nhất với
  nhau (vd. 112: 39.550.000.000 và 39.784.301.712) → xem DBG-20.

### DBG-20 – Mã cùng tên nhưng khác nội dung qua mốc TT99 (130, 120) → semantic_break
- Giai đoạn / tầng: map TT99 / DQ
- Triệu chứng: bảng "Kỳ bị trình bày lại" ghi 2025 mã 130: cuối năm 10.182.835.978.469, đầu năm 2026
  2.172.981.816.979 (−78,66%). Mã 120 tăng gần đúng chừng ấy (41.883.889.962.478 so với 33.874.270.102.700).
- Nguyên nhân gốc: TT99 phân loại lại (khoảng 8.010 tỷ chuyển từ phải thu ngắn hạn sang đầu tư tài chính ngắn hạn);
  ghép theo tên coi TT99 130 = TT200 130.
- Cách phát hiện: ghi chú "đổi chế độ kế toán" trong bảng trình bày lại (commit `399ed86`).
- Cách xử lý: cột `semantic_break` trong bảng map: ghép theo tên mà **mọi** báo cáo 2026 lệch số TT200 và không giống
  lỗi OCR → `true` (120, 130). Cột đầu năm của báo cáo 2026 với các mã này không được so chéo (bỏ phiếu, đầu năm =
  cuối năm trước, phát hiện trình bày lại). Gold/KPI không nối chuỗi thời gian qua cờ này (kế hoạch tuần 7).
- Kiểm chứng: `config/account_code_regime_map.csv` (2 dòng `semantic_break = true`); dòng 2025/130 không còn trong
  bảng trình bày lại; test cho logic cờ ở tool: chưa có.
- Commit: commit thêm mục này.
- Bài học / hạn chế còn lại: 110, 112, 310 có báo cáo 2026 khớp TT200 và báo cáo khác lệch → nghi trình bày lại giữa
  các báo cáo 2026, chưa kết luận.

### DBG-21 – Bug: bỏ phiếu điền mã TT200 vào báo cáo TT99 (mã 135)
- Giai đoạn / tầng: tự sửa
- Triệu chứng: `130 = Σ con` của TT99 trượt 4/4 báo cáo; báo cáo 2026 có thêm ô "135" = 6.910.500.000.000
  (`value_origin = consensus_filled`) cạnh ô 135 OCR đúng (mã chuẩn 136).
- Nguyên nhân gốc: bước điền ô thiếu của `apply_consensus` tạo ô với `account_code` = **mã chuẩn TT200**; với báo cáo
  TT99 cùng số mã là chỉ tiêu khác (TT200 135 "phải thu về cho vay").
- Cách phát hiện: thống kê phương trình cha–con trượt > 50% báo cáo.
- Cách xử lý: chỉ điền ô thiếu vào báo cáo TT200 (mã in = mã chuẩn) (`self_correct.apply_consensus`).
- Kiểm chứng: `tests/test_self_correct.py::test_khong_dien_o_thieu_vao_bao_cao_tt99`; `130 = Σ con` TT99 hết trượt.
- Commit: `399ed86`.
- Bài học / hạn chế còn lại: mọi chỗ dùng mã chuẩn để **ghi** phải đổi ngược về mã in; ô TT99 thiếu chưa được điền.

### DBG-22 – Kiểm `60 = 61 + 62`
- Giai đoạn / tầng: DQ
- Triệu chứng: TT200 B02 60 "critical chưa kiểm được": `60 = 50 - 51 - 52` cần sign của 52 (thuế hoãn lại, có thể
  âm hoặc dương), dim_account để trống.
- Nguyên nhân gốc: thiếu phương trình đánh giá được cho LNST.
- Cách phát hiện: dòng "Critical chưa kiểm được" trong `docs/dq_report.md`.
- Cách xử lý: thêm `60 = 61 + 62` (cổ đông công ty mẹ + cổ đông không kiểm soát) cho TT200 và TT99 vào
  `config/dq_equations.csv`, mức critical.
- Kiểm chứng: "Critical chưa kiểm được" của TT200 còn B02 50 (báo cáo OCR mất mã 30/40); `60 = 61 + 62` trượt 7/48 báo cáo TT200 (top 10 trong `docs/dq_report.md`).
- Commit: commit thêm mục này.
- Bài học / hạn chế còn lại: phương trình mới làm lộ thêm lỗi OCR ở 61/62 (top 10).

### DBG-23 – docs/reference không tìm thấy; file .doc đọc bằng LibreOffice
- Giai đoạn / tầng: hạ tầng / dim_account
- Triệu chứng: văn bản tham chiếu người dùng đặt ở Windows, phiên làm việc chạy trong WSL không thấy; văn bản là `.doc`.
- Nguyên nhân gốc: khác đường dẫn Windows và WSL (chi tiết đường dẫn: chưa xác minh, không có trong repo).
- Cách phát hiện: chưa xác minh (trao đổi trong phiên, không có log).
- Cách xử lý: người dùng chép vào `docs/reference/` trong repo; đọc `.doc` bằng
  `soffice --headless --convert-to "txt:Text (encoded):UTF8"` (hướng dẫn ở đầu `tools/build_dim_account.py`).
- Kiểm chứng: `config/dim_account.csv` 287 dòng (TT200 140, TT99 147) dựng từ `docs/reference/`.
- Commit: `1438645`.
- Bài học / hạn chế còn lại: dùng đường dẫn tương đối trong repo; bản TT202 chính thức vẫn chưa có trong
  `docs/reference/`.

### DBG-24 – Builder dim_account coi số hạng đầu của mọi công thức là phép trừ
- Giai đoạn / tầng: dim_account
- Triệu chứng: công thức cha–con dựng ra có dấu trừ ở số hạng đầu.
- Nguyên nhân gốc: Python `"" in "-–"` là `True`; số hạng đầu không có dấu nên chuỗi dấu rỗng bị coi là dấu trừ.
- Cách phát hiện: chưa xác minh (sửa trước khi commit).
- Cách xử lý: `s and s in "-–"` (`tools/build_dim_account.py`, dòng phân tích công thức).
- Kiểm chứng: chưa có test cho `tools/build_dim_account.py`.
- Commit: `1438645`.
- Bài học / hạn chế còn lại: thêm test cho builder khi có bản TT202 chính thức.

## DQ và chính sách dữ liệu

### DBG-25 – Tổng dòng con trượt giả vì ô in "-"
- Giai đoạn / tầng: DQ
- Triệu chứng: dòng tổng bị báo lệch khi một dòng con in "-".
- Nguyên nhân gốc: "-" được đọc thành null, null bị coi là thiếu.
- Cách phát hiện: chưa xác minh (số liệu trước khi sửa không lưu).
- Cách xử lý: ô in "-" (null) là 0 khi cộng dòng con; ô không đọc được hẳn vẫn là thiếu; bỏ qua dòng tổng có dòng con
  chưa chắc cộng/trừ (`pdf_extractor.check_parent_sums`).
- Kiểm chứng: `tests/test_extraction.py::test_tong_dong_con_coi_o_gach_ngang_la_0`, `test_check_parent_sums_theo_dim_account`.
- Commit: `1438645`.
- Bài học / hạn chế còn lại: phân biệt "nguồn in rỗng" với "OCR không đọc được".

### DBG-26 – Số tiền vượt int64 khi OCR dính hai số
- Giai đoạn / tầng: bóc tách
- Triệu chứng: ghi Delta lỗi tràn int64.
- Nguyên nhân gốc: OCR dính hai cột số thành một chuỗi chữ số rất dài.
- Cách phát hiện: lỗi khi ghi `fs_extracted_raw` (log không lưu: chưa xác minh chi tiết).
- Cách xử lý: chặn số > 16 chữ số (lỗi bóc tách); số tiền đọc dễ tính với dấu phân cách OCR sai.
- Kiểm chứng: `tests/test_extraction.py::test_parse_amount_sai`, `test_parse_amount_de_tinh_voi_dau_phan_cach_ocr`.
- Commit: `8690c2e` ("chặn số > 16 chữ số").
- Bài học / hạn chế còn lại: –

### DBG-27 – DQ "tất cả hoặc không" cho 0/52 → mức critical/warning, chấp nhận theo ô
- Giai đoạn / tầng: DQ
- Triệu chứng: "BCTC qua DQ" 0/52 ở commit `8690c2e` và `161cd7a` dù B01 cân đối 49/52: một ô dòng con lệch làm cả
  báo cáo bị loại.
- Nguyên nhân gốc: tiêu chí quá thô cho dữ liệu OCR.
- Cách phát hiện: so B01 cân đối (94%) với qua DQ (0%).
- Cách xử lý: critical (B01 cân đối; B02 10/20/30/50/60; kỳ/ngày; nhãn cột) chặn Gold; warning (tổng dòng con mục nhỏ,
  đầu năm ≠ cuối năm trước) chỉ ghi lại; ô **trusted** khi nằm trong một phương trình đúng, có ≥ 2 nguồn bỏ phiếu khớp,
  hoặc là đồng nhất thức của ô trusted. Từ commit này chặn **theo từng báo cáo**: ô B01 chỉ cần critical B01, ô B02
  chỉ cần critical B02 (`passed_critical_b01`, `passed_critical_b02`, `gold_eligible` theo ô).
- Kiểm chứng: `tests/test_self_correct.py::test_dq_level`, `test_o_trusted_va_critical`; chỉ số ở mục cuối.
- Commit: `399ed86` (critical/trusted), commit thêm mục này (theo từng báo cáo).
- Bài học / hạn chế còn lại: B03 chưa có critical nên chưa vào Gold.

### DBG-28 – Số "trình bày lại" thực ra là lỗi OCR thừa chữ số (2024 mã 153, 228)
- Giai đoạn / tầng: DQ
- Triệu chứng: bảng "Kỳ bị trình bày lại": 2024 mã 153 cuối năm 332.630.294.575 so với đầu năm mọi báo cáo 2025
  32.630.294.575 (lệch đúng 300 tỷ); 228: 900.297.772.827 so với 90.297.772.827 (lệch đúng 810 tỷ).
- Nguyên nhân gốc: OCR đọc thừa một chữ số ở BCTC 2024; quy tắc "mọi báo cáo N+1 khớp nhau → trình bày lại" không
  phân biệt được.
- Cách phát hiện: chênh lệch tròn và chuỗi số chỉ khác một chữ số chèn thêm.
- Cách xử lý: ghi chú "nghi OCR đọc thừa/thiếu một chữ số" trong bảng; sinh gợi ý pending cho ô cuối năm N
  (`run_extraction.digit_suggestions`, `suggested_by = adjacent_period`), người dùng xác nhận trên ảnh PDF.
- Kiểm chứng: `tests/test_self_correct.py::test_goi_y_lech_mot_chu_so_so_voi_ky_lien_ke`, `test_ghi_chu_trinh_bay_lai`;
  2 dòng pending mới trong `config/manual_overrides.csv` (BCTC 2024, mã 153 và 228).
- Commit: `399ed86` (ghi chú), commit thêm mục này (gợi ý pending).
- Bài học / hạn chế còn lại: chưa xác nhận trên PDF (status vẫn pending).

### DBG-29 – vnstock không còn trên PyPI → đối soát bằng báo cáo tháng của MWG
- Giai đoạn / tầng: Silver (đối soát)
- Triệu chứng: `pip install vnstock`: `pypi.org/simple/vnstock` trả 404 (ghi chú ngày 11/11/2026 trong `requirements.txt`).
- Nguyên nhân gốc: gói bị gỡ khỏi PyPI.
- Cách phát hiện: cài đặt tuần 1.
- Cách xử lý: tạm tắt vnstock (`requirements.txt`); đối soát B02 mã 10 (doanh thu thuần) và 60 (LNST, thử cả 61) với
  báo cáo KQKD tháng lũy kế 3/6/9/12 do MWG công bố (`src/quality/monthly_check.py`): số BCTC làm tròn tỷ đồng có
  xuất hiện (±1 tỷ) trong báo cáo tháng cùng kỳ không. Chỉ ghi `docs/dq_report.md`, không ghi Gold.
- Kiểm chứng: `tests/test_self_correct.py::test_bao_cao_thang_so_va_ky`; kết quả ở mục "Đối soát B02 mã 10, 60 với
  báo cáo tháng lũy kế" của `docs/dq_report.md`.
- Commit: `3cabe10` (tắt vnstock), commit thêm mục này (đối soát báo cáo tháng).
- Bài học / hạn chế còn lại: báo cáo tháng làm tròn tỷ đồng nên chỉ xác nhận độ lớn, không xác nhận từng chữ số;
  báo cáo 2024–2026 chỉ in doanh thu; "không thấy" không có nghĩa là sai. Kết quả: mã 10 thấy 15/28 ô, mã 60 thấy
  12/25 ô. Bảng ghi số cùng độ dài gần nhất; lệch đúng một chữ số (≥ 4 chữ số) thì ghi "nghi OCR": H1/2025 doanh thu
  BCTC đọc 73.755 tỷ, báo cáo tháng in 73.655 tỷ (ô chưa vào Gold, cần xem PDF). Lệch lớn (vd. Q3/2026: 147.827 so với
  147.953) có thể do báo cáo tháng là số sơ bộ. Báo cáo tháng 2022-M07 trở đi nhiều file không có lớp text, OCR 100 dpi
  đọc kém (vd. 2023-M12 chỉ thấy "118 ngàn tỷ").

### DBG-30 – Chặn Gold B02 theo cả báo cáo làm mất cột quý đúng
- Giai đoạn / tầng: DQ → Gold
- Triệu chứng: báo cáo quý có cột "lũy kế" trượt `20 = 10 − 11` (vd. thiếu ô 11) thì cả 4 cột B02 bị loại khỏi Gold,
  kể cả cột "quý này" đã cân đối.
- Nguyên nhân gốc: `passed_critical_b02` tính trên mọi phương trình critical của mọi cột.
- Cách phát hiện: so số ô trusted B02 (nhiều) với ô `gold_eligible` B02 (950).
- Cách xử lý: `run_extraction.critical_columns()`: critical B02 tính theo `(statement, period_kind)`; điều kiện chung
  của báo cáo (trang B02, ngày/kỳ, nhãn cột TT99) vẫn áp cho mọi cột; cột không có phương trình critical nào kiểm được
  thì không qua. `passed_critical_b02` cấp báo cáo giữ lại để báo cáo DQ.
- Kiểm chứng: `tests/test_extraction.py::test_critical_columns_chan_b02_theo_cot`. Ô B02 vào Gold **950 → 1.446**
  (2.716 ô có số); 90/128 cột B02 qua critical.
- Commit: commit Phần A (13/11/2026).
- Bài học / hạn chế còn lại: cột "năm trước" (`q_prior`, `prior`) giờ có thể vào Gold dù cột năm nay trượt; Silver
  vẫn xếp số gốc trên số so sánh.

### DBG-31 – `map_tt99_numeric.py` ghi "cuối kỳ BCTC None" vào bảng map
- Giai đoạn / tầng: map TT99
- Triệu chứng: cột `source` của 6 dòng `numeric_31122025` trong `config/account_code_regime_map.csv` ghi
  "cuối kỳ BCTC None" hoặc "cuối kỳ BCTC 50769502237457".
- Nguyên nhân gốc: trong vòng lặp, `ref = v200.get(...)` ghi đè biến `ref` (kỳ so sánh, `--ref 2025`). Ghép mã không
  sai (bảng lọc theo `ref` trước vòng lặp), chỉ hỏng chuỗi nguồn.
- Cách phát hiện: viết test cho tool (Phần A.3): assert `"cuối kỳ BCTC 2025" in source`.
- Cách xử lý: tách lõi thành `match_numeric()` (thuần, test được), đổi tên biến thành `ref_v`; chạy lại
  `python tools/map_tt99_numeric.py --ref 2025`: chỉ 6 chuỗi `source` đổi, không mã nào đổi ghép.
- Kiểm chứng: `tests/test_tools.py::test_match_numeric_cac_truong_hop`. `tools/build_dim_account.py` cũng được bọc
  `main()` để import được trong test (`test_build_rows_sign_va_cha` phủ DBG-24).
- Commit: commit Phần A (13/11/2026).
- Bài học / hạn chế còn lại: script "chạy một lần" vẫn cần test; biến dùng lại tên trong vòng lặp dài.

### DBG-32 – Đối soát báo cáo tháng: lệch một chữ số → đọc lại ô trên PDF BCTC
- Giai đoạn / tầng: Silver (đối soát)
- Triệu chứng: 7 ô B02 (10/61) lệch báo cáo tháng đúng một chữ số (tỷ đồng), vd. H1/2025 mã 10: BCTC 73.755 tỷ, báo
  cáo tháng 2025-M06 in 73.655 tỷ.
- Nguyên nhân gốc: chưa xác định bên nào sai.
- Cách phát hiện: bảng đối soát ở `docs/dq_report.md` (DBG-29).
- Cách xử lý: `run_extraction.monthly_suggestions()`: với ô lệch ≥ 1 tỷ và khác đúng một chữ số, OCR lại riêng ô đó
  trên PDF BCTC (400/500/600 dpi); số đọc lại khớp báo cáo tháng (±1 tỷ) thì ghi pending
  (`suggested_by = monthly_crosscheck`), không thì ghi kết quả đọc lại vào bảng đối soát.
- Kiểm chứng: lượt chạy 13/11: **0 gợi ý**. H1/2025 mã 10 đọc lại vẫn ra 73.755 tỷ; xem ảnh trang 9 của BCTC bán niên
  2025 (sha `8317b8bf…`): in "73.754.927.078.179", và `10 = 01 − 02` đúng (74.177.074.158.082 − 422.147.079.903). Báo
  cáo tháng 2025-M06 (lớp text, không OCR) in "73.655": lệch nằm ở báo cáo tháng. 2022-H1, 2022 mã 61 cũng đọc lại ra
  số cũ (lệch 2 tỷ, nghi báo cáo tháng sơ bộ). `tests/test_extraction.py::test_monthly_suggestions_chi_goi_y_khi_doc_lai_khop`.
- Commit: commit Phần A (13/11/2026).
- Bài học / hạn chế còn lại: báo cáo tháng không phải nguồn chuẩn; chỉ dùng để chỉ ra ô cần xem. H1/2025 B02 cột
  `current` vẫn không vào Gold vì ô 11 đọc thành null (raw_text có số), không phải vì mã 10.

### DBG-33 – Nhãn cột B02 quý cũ gán theo vị trí sai (2014-Q3, 2016-Q3) và Q2/2026 TT99 bị loại
- Giai đoạn / tầng: bóc tách → Silver
- Triệu chứng: Silver mục "Kỳ bị trình bày lại" có 17 ô B02 2016-Q3 lệch 100–4.000% với cột so sánh của BCTC
  2017-Q3: "lũy kế 9T/2016" = 6.647 tỷ, trong khi BCTC 2017-Q3 in 9T/2016 = 30.776 tỷ. Q2/2026 (TT99) không vào Silver
  vì `columns_ok = false`.
- Nguyên nhân gốc: (1) gán nhãn theo vị trí giả định thứ tự cột của BCTC 2023+ (quý này, lũy kế, quý năm trước, lũy kế
  năm trước); BCTC quý 2014 và 2016 in quý này, quý năm trước, lũy kế, lũy kế năm trước. Phương trình 10 = 01 − 02 vẫn
  đúng trên từng cột nên nhãn sai qua critical và vào Gold. (2) `relabel_b02_numeric` tìm cột `ytd_current` của báo cáo
  Q1 cùng năm, nhưng báo cáo Q1 chỉ có hai cột `current/prior`.
- Cách phát hiện: so số gốc với số so sánh ở Silver (Phần B.4).
- Cách xử lý: `run_extraction.check_positional_order()` (chạy sau PaddleOCR): B02 quý Q2–Q4 bốn cột gán theo vị trí
  phải có lũy kế > quý (doanh thu, cả hai năm); chỉ thứ tự cũ đúng thì gắn lại nhãn (`b02_label_method = numeric`).
  `relabel_b02_numeric` dùng cột `current` của báo cáo Q1 hai cột.
- Kiểm chứng: `tests/test_extraction.py::test_check_positional_order_doi_thu_tu_cot_cu`; 2014-Q3, 2016-Q3 và 2026-Q2
  chuyển sang `numeric`; ô trình bày lại ở Silver 55 → 38 (trước khi chuẩn hóa dấu, DBG-34).
- Commit: commit Phần B (13/11/2026).
- Bài học / hạn chế còn lại: báo cáo hai cột không kiểm được bằng lũy kế > quý; đối chiếu số gốc ↔ số so sánh là phép
  kiểm nhãn cột mạnh nhất hiện có.

### DBG-34 – Khoản giảm trừ in dấu khác nhau giữa các báo cáo
- Giai đoạn / tầng: Silver
- Triệu chứng: 21 ô "trình bày lại" lệch đúng −200% (vd. 2021 B02 11: BCTC 2021 = +95.325 tỷ, cột so sánh BCTC 2022
  = −95.325 tỷ); Q2 = H1 − Q1 sẽ sai khi hai báo cáo in khác dấu.
- Nguyên nhân gốc: TT200 in khoản giảm trừ trong ngoặc (âm), TT99 in dương, và OCR đôi khi mất ngoặc.
- Cách phát hiện: bảng trình bày lại của Silver.
- Cách xử lý: Silver lưu mọi mã chuẩn có `sign = -1` trong `dim_account` (TT200) là số âm (`-abs`), đúng tinh thần
  "trị tuyệt đối chỉ cho mã có sign = -1" của DQ.
- Kiểm chứng: `tests/test_silver.py::test_build_long_dau_ma_khong_anh_xa_va_cot_b02_chua_chung_minh`; trình bày lại
  38 → 17 ô (còn lại: 421a/421b 2017, chi phí/lãi Q4/2023 trình bày lại ở BCTC Q4/2024, 112/136/310 đầu năm 2026 theo TT99).
- Commit: commit Phần B (13/11/2026).
- Bài học / hạn chế còn lại: mã sign trống (52) giữ dấu in.

---

## Bảng tóm tắt

| ID | Tầng | Mức ảnh hưởng | Trạng thái |
|---|---|---|---|
| DBG-01 | crawl | cao (không tải được file) | đã sửa |
| DBG-02 | crawl | cao (mất 56 tài liệu vi-VN) | đã sửa, còn hạn chế (một số kỳ không có nguồn) |
| DBG-03 | crawl | trung bình | đã sửa |
| DBG-04 | crawl / Bronze | trung bình (mất 11 báo cáo tháng 2017) | đã sửa |
| DBG-05 | crawl / Bronze | trung bình | đã sửa, còn hạn chế (H1/2017, 05/2017 không có nguồn) |
| DBG-06 | crawl | trung bình | đã sửa |
| DBG-07 | OCR | cao (mọi BCTC phải OCR) | đã xử lý, còn hạn chế (độ chính xác OCR) |
| DBG-08 | OCR | cao | đã sửa |
| DBG-09 | bóc tách | cao | đã sửa |
| DBG-10 | DQ | cao (mọi phương trình trừ trượt) | đã sửa, còn hạn chế (52 chưa có sign) |
| DBG-11 | OCR | trung bình | đã sửa một phần |
| DBG-12 | OCR / DQ | cao (440 trượt 34/48) | đã sửa (đồng nhất thức), còn hạn chế |
| DBG-13 | OCR / tự sửa | cao | đã sửa một phần; 15 ô chờ xác nhận |
| DBG-14 | bóc tách | thấp | đã sửa |
| DBG-15 | bóc tách | trung bình | đã sửa, còn hạn chế (Q2/2026) |
| DBG-16 | bóc tách | thấp | đã sửa |
| DBG-17 | DQ / dim_account | trung bình | đã xử lý bằng số liệu, chờ văn bản chính thức |
| DBG-18 | DQ | cao (B02 critical 27 → 11) | đã sửa |
| DBG-19 | map TT99 | cao | đã xử lý, còn 10 mã chưa khớp số |
| DBG-20 | map TT99 / DQ | trung bình | đã xử lý (cờ), Gold chưa dùng cờ |
| DBG-21 | tự sửa | trung bình (bug của pipeline) | đã sửa |
| DBG-22 | DQ | trung bình | đã sửa |
| DBG-23 | hạ tầng | thấp | đã xử lý |
| DBG-24 | dim_account | cao nếu lọt (sai dấu mọi công thức) | đã sửa, chưa có test |
| DBG-25 | DQ | trung bình | đã sửa |
| DBG-26 | bóc tách | thấp | đã sửa |
| DBG-27 | DQ | cao (0/52 vào Gold) | đã sửa |
| DBG-28 | DQ | thấp (2 ô) | chờ người dùng xác nhận |
| DBG-29 | Silver | trung bình | đã thay thế, còn hạn chế (làm tròn tỷ đồng) |
| DBG-30 | DQ → Gold | trung bình (B02 vào Gold 950 → 1.446) | đã sửa |
| DBG-31 | map TT99 | thấp (chỉ chuỗi nguồn) | đã sửa, có test |
| DBG-32 | Silver (đối soát) | thấp | đã xử lý: lệch thuộc báo cáo tháng |
| DBG-33 | bóc tách | cao (nhãn sai lọt Gold) | đã sửa |
| DBG-34 | Silver | cao (Q2/Q4 suy ra sai dấu) | đã sửa |

## Chỉ số chất lượng qua các lượt chạy

Số lấy từ mục tự sinh của `docs/dq_report.md` tại từng commit (`git show <commit>:docs/dq_report.md`).
"–" = chỉ số chưa có ở lượt đó. Lượt `8690c2e` không in tỷ lệ B01 cân đối; số 32/52 đếm từ bảng chi tiết theo kỳ của
chính lượt đó (báo cáo có mọi phép kiểm phương trình B01 đúng).

| Lượt (commit, giờ chạy) | B01 cân đối | OCR thô: B01 cân đối | Qua DQ toàn bộ | Qua critical (cả B01 và B02) | Critical B01 / B02 | Ô trusted | Ô vào Gold |
|---|---|---|---|---|---|---|---|
| `8690c2e` (11/11 16:11, lượt OCR đầu) | 32/52 (62%) | – | 0/52 | – | – | – | – |
| `161cd7a` (12/11 14:40) | 49/52 (94%) | – | 0/52 | – | – | – | – |
| `1438645` (12/11 15:46) | 49/52 (94%) | 34/52 (65%) | 3/52 | – | – | – | – |
| `399ed86` (12/11 16:05) | 49/52 (94%) | 34/52 (65%) | 5/52 | 28/52 (54%) | – | 7.089/11.860 (60%) | 3.917 (chặn cả báo cáo) |
| commit thêm file này (12/11) | 50/52 (96%) | 34/52 (65%) | 7/52 | 25/52 (48%) | 50/52 (96%) / 25/52 (48%) | 8.004/11.950 (67%) | B01 5.807, B02 950 (chặn theo từng báo cáo) |

Ghi chú: "qua DQ toàn bộ" 0/52 ở hai lượt đầu là tiêu chí cũ (DBG-27); "qua critical" có từ `399ed86`. Qua critical cả hai báo cáo giảm 54% → 48% ở lượt cuối vì B02 critical chặt hơn: thêm `60 = 61 + 62` và `30 = ...` (trước đó TT200 B02 30 và 60 không kiểm được, coi như không trượt). Ô vào Gold tăng 3.917 → 6.757 vì chặn theo từng báo cáo (DBG-27) và đồng nhất thức 440 (DBG-12).
