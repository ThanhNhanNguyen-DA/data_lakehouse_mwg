"""Khóa chống chạy đồng thời dùng chung cho mọi entrypoint ghi dữ liệu (run_pipeline.py, python -m src..., tools/...).

    from src.common.lock import run_locked
    if __name__ == "__main__":
        sys.exit(run_locked(main, sys.argv[1:]))

Khóa = data/_state/pipeline.lock (PID + thời điểm, tạo bằng O_EXCL). Lượt sau thấy khóa của tiến trình còn sống thì
dừng (mã 2); khóa của PID đã chết thì gỡ. run_pipeline giữ khóa rồi gọi thẳng các hàm run() bên trong (không qua
__main__) nên không tự khóa chính mình.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from src.common.paths import STATE_DIR

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


def acquire_lock(path: Path | None = None) -> None:
    """Tạo khóa (O_EXCL, nguyên tử). Khóa của tiến trình còn sống -> PipelineLocked; của PID đã chết -> gỡ.
    shortcut: PID có thể bị hệ điều hành dùng lại sau khi tiến trình cũ chết; xóa tay file khóa nếu chắc không còn lượt nào."""
    path = Path(path or LOCK)
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


def release_lock(path: Path | None = None) -> None:
    """Chỉ gỡ khóa của chính tiến trình này."""
    path = Path(path or LOCK)
    try:
        if json.loads(path.read_text(encoding="utf-8")).get("pid") == os.getpid():
            path.unlink()
    except (OSError, ValueError):
        pass


@contextmanager
def pipeline_lock(path: Path | None = None):
    acquire_lock(path)
    try:
        yield
    finally:
        release_lock(path)


def run_locked(fn, *args) -> int:
    """Chạy fn(*args) trong khóa; khóa đang bị giữ thì in lý do và trả 2. Trả mã thoát (fn trả int thì dùng)."""
    try:
        with pipeline_lock():
            res = fn(*args)
    except PipelineLocked as e:
        print(f"[lock] Dừng: {e}")
        return 2
    return res if isinstance(res, int) else 0
