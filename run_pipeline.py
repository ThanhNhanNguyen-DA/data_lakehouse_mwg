"""Chạy toàn bộ pipeline: crawl mwg.vn (incremental) -> bóc tách + DQ -> Silver -> Gold.

    python run_pipeline.py                  # incremental: crawl, chỉ chạy tiếp khi có tài liệu mới
    python run_pipeline.py --backfill       # lần đầu: crawl toàn bộ lịch sử
    python run_pipeline.py --force          # chạy lại bóc tách/Silver/Gold từ Bronze sẵn có, KHÔNG gọi mwg.vn
    python run_pipeline.py --force --crawl  # crawl rồi chạy lại toàn bộ (GitHub Actions khi chọn force)
    python run_pipeline.py --no-crawl       # bỏ bước crawl (máy offline), dùng Bronze sẵn có

Chỉ khi crawl mới gọi mạng (kể cả tải lại file Bronze thiếu theo manifest, kiểm sha256). Không crawl mà thiếu file
Bronze thì dừng.

Khóa chống chạy đồng thời: data/_state/pipeline.lock (PID + thời điểm). Lượt sau thấy khóa của tiến trình còn sống
thì dừng (mã 2); khóa của PID đã chết thì gỡ rồi chạy. Hai lượt chồng nhau từng ghi đè Silver/Gold (DBG-41).

Bóc tách dùng cache OCR (data/_cache/ocr), nên chạy lại không OCR lại file cũ. Sửa config/manual_overrides.csv
(xác nhận ô pending) thì chạy với --force để áp dụng.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from src.common.paths import STATE_DIR
from src.extraction import run_extraction
from src.ingestion import mwg_crawler
from src.transform import bronze_to_silver, silver_to_gold

LOCK = STATE_DIR / "pipeline.lock"


class PipelineLocked(RuntimeError):
    pass


def _alive(pid: int) -> bool:
    if os.name == "nt":  # Windows: os.kill(pid, 0) sẽ KẾT THÚC tiến trình, phải hỏi bằng OpenProcess
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # tiến trình của người dùng khác: vẫn còn sống
        return True
    return True


def acquire_lock(path: Path = LOCK) -> None:
    """Tạo khóa (O_EXCL, nguyên tử). Khóa của tiến trình còn sống -> PipelineLocked; của PID đã chết -> gỡ.
    shortcut: PID có thể bị hệ điều hành dùng lại sau khi tiến trình cũ chết; xóa tay file khóa nếu chắc không còn lượt nào."""
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                info = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                info = {}
            pid = info.get("pid")
            if isinstance(pid, int) and _alive(pid):
                raise PipelineLocked(f"pipeline đang chạy (PID {pid}, bắt đầu {info.get('started_at')}); "
                                     f"chờ lượt đó xong hoặc xóa {path} nếu chắc chắn không còn chạy")
            print(f"[pipeline] Gỡ khóa cũ của PID {pid} (tiến trình đã dừng)")
            path.unlink(missing_ok=True)
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "started_at": datetime.now().isoformat(timespec="seconds")}, f)
        return
    raise PipelineLocked(f"không lấy được khóa {path}")


def release_lock(path: Path = LOCK) -> None:
    """Chỉ gỡ khóa của chính tiến trình này."""
    try:
        if json.loads(path.read_text(encoding="utf-8")).get("pid") == os.getpid():
            path.unlink()
    except (OSError, ValueError):
        pass


def main(argv: list[str]) -> int:
    try:
        acquire_lock()
    except PipelineLocked as e:
        print(f"[pipeline] Dừng: {e}")
        return 2
    try:
        return _run(argv)
    finally:
        release_lock()


def _run(argv: list[str]) -> int:
    t0 = time.time()
    force = "--force" in argv
    # --force chỉ dựng lại từ Bronze sẵn có; muốn crawl kèm thì thêm --crawl
    crawl = "--no-crawl" not in argv and ("--crawl" in argv or "--backfill" in argv or not force)
    new = []
    if crawl:
        new = mwg_crawler.crawl(backfill="--backfill" in argv)
        print(f"[pipeline] Crawl: {len(new)} tài liệu mới")
    if not new and not force:
        print("[pipeline] Không có tài liệu mới. Dùng --force để chạy lại bóc tách, Silver, Gold.")
        return 0
    # repo không chứa PDF Bronze (clone mới, runner CI): tải lại theo manifest trước khi bóc tách. Thiếu file mà vẫn
    # chạy thì báo cáo đó biến mất khỏi Silver/Gold -> dừng thay vì ghi đè bằng dữ liệu thiếu.
    if mwg_crawler.missing_files().height and crawl:  # tải lại cũng là gọi mạng: chỉ khi được crawl
        mwg_crawler.restore_missing()
    still = mwg_crawler.missing_files()
    if still.height:
        print(f"[pipeline] Thiếu {still.height} file Bronze (vd. {still['path'][0]}); không chạy bóc tách. "
              "Chạy: python -m src.ingestion.mwg_crawler --restore, hoặc thêm --crawl")
        return 1
    run_extraction.run()
    bronze_to_silver.run()
    silver_to_gold.run()
    print(f"[pipeline] Xong sau {time.time() - t0:.0f} giây")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
