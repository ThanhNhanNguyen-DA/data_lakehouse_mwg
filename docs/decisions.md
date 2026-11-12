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
