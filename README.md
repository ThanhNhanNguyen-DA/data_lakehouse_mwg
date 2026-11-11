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

## 3. Chạy

```bash
python -m src.ingestion.mwg_crawler --dry-run     # xem trước tài liệu sẽ tải
python -m src.ingestion.mwg_crawler --backfill    # lần đầu: toàn bộ lịch sử (chậm, có nghỉ giữa request)
python -m src.ingestion.mwg_crawler               # incremental
python -m src.extraction.run_extraction           # OCR + bóc B01/B02/B03 + DQ (lần đầu lâu; có cache OCR)
pytest -q
```

Kết quả OCR được cache trong `data/_cache/ocr/` theo sha256 của file, chạy lại không OCR lại.
Test OCR trên PDF mẫu (`tests/test_fixtures_ocr.py`) tự bỏ qua khi máy chưa có Tesseract.
Số kỳ vọng của PDF mẫu nằm trong `tests/fixtures/expected_values.csv`: tự đọc từ PDF rồi điền.

## 4. Sửa tay khi OCR sai

Kỳ không qua DQ được sửa bằng `config/manual_overrides.csv`: đọc số đúng **từ chính PDF gốc trong
`data/bronze/`**, ghi `source_doc_sha256`, số trang, giá trị OCR sai (`ocr_value`) và lý do.
Chạy lại `run_extraction` để kiểm tra DQ của kỳ đó.
