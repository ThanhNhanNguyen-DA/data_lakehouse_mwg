# Luồng ETL/ELT — MWG Financial Data Lakehouse

Ảnh: [mwg-etl-elt-flow.png](diagrams/mwg-etl-elt-flow.png). Đối chiếu mã nguồn ngày **12/11/2026**.

Luồng hiện tại là **E → L (Bronze) → T (Python) → L (Silver raw)**: tải tài liệu từ MWG, lưu nguyên bản, bóc tách và biến đổi bằng Python, sau đó ghi bảng Delta. Đây là cách tổ chức lưu raw trước khi biến đổi, kết hợp bước ETL từ tài liệu sang dữ liệu có cấu trúc; không có bước SQL transform trong warehouse đang chạy.

```mermaid
flowchart TB
    subgraph CURRENT["Đã có mã: E → L Bronze → T Python → L Silver RAW"]
        direction LR
        E["E · Extract<br/>MWG IR: CategoryAPI → PostAPI → tải file<br/>mwg_crawler.py · vi-VN chính / en-US dự phòng"]
        L1[("L · Bronze<br/>PDF / JPG / PNG + .meta.json<br/>Append-only · SHA256")]
        T["T · Transform bằng Python<br/>run_extraction.py<br/>OCR · dựng bảng · chuẩn hóa · tự sửa · DQ"]
        L2[("L · Silver RAW<br/>data/silver/fs_extracted_raw<br/>Polars → Delta Lake · overwrite<br/>Lưu cả ô chưa đạt DQ cùng cờ kiểm tra")]
        E --> L1 --> T --> L2
    end

    STATE[("data/_state/manifest.parquet<br/>URL · SHA256 · đường dẫn · kỳ · has_text_layer")]
    E <--> STATE
    STATE --> T

    subgraph DETAIL["Chi tiết bước T — luồng chính và các lần kiểm tra lại"]
        direction LR
        READ["Đọc và dựng bảng<br/>Text PDF: pdfplumber<br/>Scan / ảnh: Tesseract vie+eng<br/>OCR lại vùng lỗi; cache kết quả"]
        NORM["Chuẩn hóa và đối chiếu<br/>Ô B01 / B02 / B03; tiền VND<br/>Mã chuẩn theo cấu hình TT200 / TT99<br/>Nhãn cột B02; kỳ báo cáo<br/>Consensus B01 giữa các báo cáo"]
        DQ["Kiểm tra và gắn cờ<br/>Phương trình; tổng dòng con; Q1<br/>Ngày báo cáo; số đầu năm / cuối năm<br/>Phân biệt critical và warning<br/>passed_critical; is_trusted"]
        PADDLE["PaddleOCR dự phòng<br/>Khi còn sai phương trình đánh giá được<br/>Giữ kết quả nếu giảm số lỗi"]
        SUGGEST["Gợi ý sửa ô theo phương trình<br/>manual_overrides.csv · status=pending"]
        HUMAN["Đọc PDF gốc và xác nhận<br/>Gợi ý chỉ áp dụng khi confirmed<br/>Chạy lại bóc tách và DQ"]
        REPORT["docs/dq_report.md"]
        READ --> NORM --> DQ
        DQ --> PADDLE --> DQ
        DQ --> SUGGEST --> HUMAN --> NORM
        DQ --> REPORT
    end

    MONTHLY["Nhánh báo cáo tháng<br/>Hiện chỉ kiểm tra kỳ trên trang đầu<br/>Chưa bóc chain_kpi"]
    L1 --> MONTHLY --> REPORT
    FLAGS["Cờ tính trong code hiện tại:<br/>gold_eligible = passed_critical AND is_trusted<br/>Cờ được lưu trong raw; chưa nạp sang Gold"]

    subgraph FUTURE["Dự kiến — chưa có mã transform / Gold / dashboard"]
        direction LR
        SILVER["Silver chuẩn hóa<br/>Chọn bản ưu tiên · Delta MERGE<br/>fin_statement_long; chain_kpi"]
        GOLD["Gold<br/>fact_financial + dimensions<br/>Delta Lake · DuckDB · KPI"]
        DASH["Dashboard<br/>Streamlit · Plotly"]
        SILVER -.-> GOLD -.-> DASH
    end

    L2 -.-> SILVER

    classDef existing fill:#eef8f5,stroke:#168474,color:#143747;
    classDef bronze fill:#fff1dd,stroke:#b47e40,color:#49311c;
    classDef raw fill:#eaf2ff,stroke:#5279b1,color:#19354f;
    classDef note fill:#fff7df,stroke:#bd933a,color:#5b4819;
    classDef future fill:#f4f5f7,stroke:#949ba8,stroke-dasharray:6 4,color:#536070;
    class E,T,STATE,READ,NORM,DQ,PADDLE,REPORT,MONTHLY existing;
    class L1 bronze;
    class L2 raw;
    class SUGGEST,HUMAN,FLAGS note;
    class SILVER,GOLD,DASH future;
```

Sơ đồ tóm tắt theo nhóm công việc. Code thực hiện DQ nhiều lần trong quá trình tự sửa; không phải chỉ kiểm tra một lần ở cuối. PaddleOCR chỉ chạy khi được bật và còn lỗi phương trình phù hợp. Khi giữ được kết quả tốt hơn, code chạy lại consensus và DQ.

Gợi ý sửa tự động được ghi với `status=pending`, không tự áp dụng. Khi người dùng đã đối chiếu PDF gốc và xác nhận, lần chạy sau mới áp dụng gợi ý đó. Bộ nạp override cũng chấp nhận các dòng cũ có status trống.

`gold_eligible` hiện là cờ tại từng ô, được tính bằng `passed_critical and is_trusted`. Bảng raw vẫn giữ các ô có cờ false. Cờ này không có nghĩa đã tồn tại luồng nạp Gold, cũng không thay thế trạng thái qua toàn bộ DQ.

Mã nguồn dùng để đối chiếu:

- [mwg_crawler.py](../src/ingestion/mwg_crawler.py): Extract, Bronze append-only, manifest và gán kỳ.
- [run_extraction.py](../src/extraction/run_extraction.py): điều phối bóc tách, chuẩn hóa, kiểm tra, fallback, tính cờ và ghi Delta raw.
- [ocr.py](../src/extraction/ocr.py): đọc lớp text, Tesseract, OCR lại vùng lỗi và PaddleOCR.
- [pdf_extractor.py](../src/extraction/pdf_extractor.py): dựng bảng, số tiền, chế độ kế toán, DQ và áp dụng override.
- [self_correct.py](../src/extraction/self_correct.py): consensus B01 và ghi gợi ý sửa chờ xác nhận.
- `src/transform/` hiện chỉ có `__init__.py`; `dashboard/` trống.

Ảnh được tạo bằng công cụ `image_gen`, [prompt đầy đủ](diagrams/mwg-etl-elt-flow.prompt.txt). Bản sơ đồ kiến trúc trước đó được giữ nguyên.

