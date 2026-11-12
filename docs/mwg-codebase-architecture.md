# Sơ đồ codebase MWG Financial Data Lakehouse

Ảnh: [mwg-codebase-architecture.png](diagrams/mwg-codebase-architecture.png).

Đối chiếu mã nguồn ngày **12/11/2026**. Nét liền biểu thị phần đã có mã; nét đứt biểu thị lộ trình trong `CLAUDE.md`.

Luồng hiện có kết thúc tại **Silver raw** (`data/silver/fs_extracted_raw`), chưa có bước chuẩn hóa Silver, Gold hoặc dashboard. Bảng raw được ghi đè và giữ cả ô không qua DQ, kèm `dq_balance_ok` và `period_check_ok`; DQ ở đây không phải bộ lọc trước khi lưu raw.

```mermaid
flowchart TB
    subgraph ING["01 · Thu thập và lưu tài liệu gốc — đã có mã"]
        direction LR
        MWG["MWG IR · mwg.vn/bao-cao<br/>CategoryAPI → PostAPI<br/>vi-VN chính · en-US dự phòng"]
        CRAWL["mwg_crawler.py<br/>Phân loại tài liệu và kỳ<br/>Incremental / backfill"]
        HTTP["http_utils.py<br/>robots · throttle · retry"]
        CONFIG["config/sources.yaml"]
        BRONZE[("Bronze · append-only<br/>PDF / JPG / PNG + .meta.json<br/>SHA256")]
        STATE[("data/_state/manifest.parquet<br/>URL · đường dẫn · SHA256<br/>has_text_layer · kỳ")]
        LOG["download_errors.csv<br/>link_anomalies.csv"]
        MWG --> CRAWL
        CONFIG --> CRAWL
        HTTP --- CRAWL
        CRAWL --> BRONZE
        CRAWL <--> STATE
        CRAWL --> LOG
    end

    subgraph EXT["02 · run_extraction.py — đã có mã"]
        direction LR
        INPUT["Tài liệu + metadata"]
        TEXT{"has_text_layer?"}
        PDF["pdfplumber<br/>Từ + tọa độ"]
        OCR["pypdfium2 + Tesseract vie+eng<br/>100 dpi: tìm trang<br/>300 dpi: OCR trang và từng cột"]
        CACHE[("data/_cache/ocr/<br/>SHA256 + trang + dpi")]
        PARSE["pdf_extractor.py<br/>Dựng B01 / B02 / B03<br/>Mã số · kỳ cột · số tiền VND"]
        OVERRIDE["config/manual_overrides.csv<br/>Áp dụng cho đúng ô / SHA256"]
        RULES["dq_equations.csv: TT200 / TT99<br/>dim_account.csv và regime map:<br/>hiện chỉ có header"]
        DQ["Kiểm tra DQ<br/>Ngày báo cáo khớp kỳ<br/>Phương trình theo regime<br/>Đầu năm = cuối năm trước khi có"]
        RAW[("Silver RAW · fs_extracted_raw<br/>Polars → Delta Lake · overwrite<br/>Giữ ô lỗi + cờ DQ")]
        REPORT["docs/dq_report.md"]
        MONTHLY["Báo cáo tháng<br/>Mới kiểm tra kỳ trên trang đầu"]
        INPUT --> TEXT
        TEXT -->|Có| PDF
        TEXT -->|Không| OCR
        OCR <--> CACHE
        PDF --> PARSE
        OCR --> PARSE
        PARSE --> OVERRIDE --> DQ
        RULES --> DQ
        DQ --> RAW
        DQ --> REPORT
        INPUT --> MONTHLY --> REPORT
    end

    BRONZE --> INPUT
    STATE --> INPUT
    DQ -->|"resolve_period(): gán kỳ pending khi xác định được"| STATE

    subgraph FUTURE["03 · Dự kiến — chưa có mã triển khai"]
        direction LR
        SILVER["Silver chuẩn hóa<br/>fin_statement_long · chain_kpi<br/>Delta MERGE"]
        GOLD["Gold · Star schema · Delta Lake<br/>DuckDB · KPI"]
        DASH["Dashboard<br/>Streamlit · Plotly"]
        ORCH["Điều phối: run_pipeline.py<br/>GitHub Actions"]
        SILVER -.-> GOLD -.-> DASH
    end

    RAW -.-> SILVER

    classDef existing fill:#eef8f5,stroke:#168474,color:#143747;
    classDef bronze fill:#fff1dd,stroke:#b47e40,color:#49311c;
    classDef raw fill:#eaf2ff,stroke:#5279b1,color:#19354f;
    classDef future fill:#f4f5f7,stroke:#949ba8,stroke-dasharray:6 4,color:#536070;
    classDef note fill:#fff7df,stroke:#bd933a,color:#5b4819;
    class MWG,CRAWL,HTTP,CONFIG,STATE,LOG,INPUT,TEXT,PDF,OCR,CACHE,PARSE,OVERRIDE,DQ,REPORT,MONTHLY existing;
    class BRONZE bronze;
    class RAW raw;
    class SILVER,GOLD,DASH,ORCH future;
    class RULES note;
```

Các chi tiết được kiểm tra trực tiếp:

- [Crawler](../src/ingestion/mwg_crawler.py): danh sách JSON API, ưu tiên ngôn ngữ, manifest, Bronze và gán lại kỳ.
- [HTTP](../src/ingestion/http_utils.py): robots, throttle, retry và xử lý lỗi.
- [Điều phối bóc tách](../src/extraction/run_extraction.py): nạp manifest, chạy nhiều process, áp dụng override, kiểm tra DQ, ghi Delta raw và báo cáo.
- [OCR](../src/extraction/ocr.py): hai lượt OCR, đọc lớp text, cache và OCR từng cột.
- [Dựng bảng và DQ](../src/extraction/pdf_extractor.py): dựng dòng/cột, nhận diện regime, phương trình và override.
- `src/transform/` và `src/quality/` hiện chỉ có `__init__.py`; `dashboard/` trống. Chưa có `run_pipeline.py` hoặc workflow GitHub Actions.

Ảnh được tạo bằng công cụ `image_gen`. [Prompt đầy đủ](diagrams/mwg-codebase-architecture.prompt.txt).

