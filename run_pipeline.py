"""Chạy toàn bộ pipeline: crawl mwg.vn (incremental) -> bóc tách + DQ -> Silver -> Gold.

    python run_pipeline.py               # incremental: chỉ chạy tiếp khi có tài liệu mới
    python run_pipeline.py --backfill    # lần đầu: crawl toàn bộ lịch sử
    python run_pipeline.py --force       # chạy lại bóc tách/Silver/Gold dù không có tài liệu mới
    python run_pipeline.py --no-crawl    # bỏ bước crawl (máy offline), dùng Bronze sẵn có

File Bronze có trong manifest mà thiếu trên máy (PDF không nằm trong git) được tải lại từ URL gốc, kiểm sha256.

Bóc tách dùng cache OCR (data/_cache/ocr), nên chạy lại không OCR lại file cũ. Sửa config/manual_overrides.csv
(xác nhận ô pending) thì chạy với --force để áp dụng.
"""
from __future__ import annotations

import sys
import time

from src.extraction import run_extraction
from src.ingestion import mwg_crawler
from src.transform import bronze_to_silver, silver_to_gold


def main(argv: list[str]) -> int:
    t0 = time.time()
    force = "--force" in argv
    new = []
    if "--no-crawl" not in argv:
        new = mwg_crawler.crawl(backfill="--backfill" in argv)
        print(f"[pipeline] Crawl: {len(new)} tài liệu mới")
    if not new and not force:
        print("[pipeline] Không có tài liệu mới. Dùng --force để chạy lại bóc tách, Silver, Gold.")
        return 0
    # repo không chứa PDF Bronze (clone mới, runner CI): tải lại theo manifest trước khi bóc tách. Thiếu file mà vẫn
    # chạy thì báo cáo đó biến mất khỏi Silver/Gold -> dừng thay vì ghi đè bằng dữ liệu thiếu.
    if mwg_crawler.missing_files().height and "--no-crawl" not in argv:
        mwg_crawler.restore_missing()
    still = mwg_crawler.missing_files()
    if still.height:
        print(f"[pipeline] Thiếu {still.height} file Bronze (vd. {still['path'][0]}); không chạy bóc tách. "
              "Chạy: python -m src.ingestion.mwg_crawler --restore")
        return 1
    run_extraction.run()
    bronze_to_silver.run()
    silver_to_gold.run()
    print(f"[pipeline] Xong sau {time.time() - t0:.0f} giây")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
