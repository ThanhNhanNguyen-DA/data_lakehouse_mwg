"""Mọi request HTTP ra ngoài (mwg.vn, CDN chứa PDF) đi qua get() trong file này.

- Một session dùng chung, header trình duyệt, Accept-Language vi-VN, Referer.
- Nghỉ ngẫu nhiên 2-5 giây giữa các request trang, 5-10 giây khi tải PDF.
- Retry exponential backoff (tối đa 4 lần) cho 429/5xx/timeout/lỗi kết nối.
- Tôn trọng robots.txt của từng host.
- Gặp 403 thì dừng (PermissionError) và ghi log, không tìm cách vượt qua.

Khi debug, đặt biến môi trường MWG_HTTP_CACHE=1 để bật requests-cache,
tránh gọi lại mwg.vn cho cùng một URL.
"""
from __future__ import annotations

import os
import random
import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.common.paths import STATE_DIR

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0",
]
PAGE_DELAY = (2.0, 5.0)
PDF_DELAY = (5.0, 10.0)
TIMEOUT = 30
RETRY_STATUS = {429, 500, 502, 503, 504}


class Retryable(Exception):
    """Lỗi tạm thời (429/5xx), sẽ được retry."""


class RobotsDisallowed(Exception):
    """robots.txt không cho phép tải URL này."""


def _make_session() -> requests.Session:
    if os.environ.get("MWG_HTTP_CACHE") == "1":
        import requests_cache

        STATE_DIR.mkdir(parents=True, exist_ok=True)
        session: requests.Session = requests_cache.CachedSession(
            str(STATE_DIR / "http_cache"), backend="sqlite", expire_after=7 * 24 * 3600
        )
    else:
        session = requests.Session()
    # Chọn một User-Agent cho cả phiên (đổi giữa các lần chạy), như một trình duyệt thật
    session.headers.update({
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/pdf,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
    })
    return session


_session = _make_session()
_last_request_at: float | None = None
_robots: dict[str, RobotFileParser] = {}


def _is_cached(url: str) -> bool:
    cache = getattr(_session, "cache", None)
    try:
        return bool(cache and cache.contains(url=url))
    except Exception:
        return False


def _throttle(delay: tuple[float, float], url: str) -> None:
    """Đảm bảo khoảng nghỉ ngẫu nhiên kể từ request trước (bỏ qua nếu đã có trong cache)."""
    if _last_request_at is None or _is_cached(url):
        return
    wait = random.uniform(*delay) - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)


def _mark_request() -> None:
    global _last_request_at
    _last_request_at = time.monotonic()


def _robots_for(url: str) -> RobotFileParser:
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    if origin in _robots:
        return _robots[origin]

    rp = RobotFileParser(origin + "/robots.txt")
    _throttle(PAGE_DELAY, rp.url)
    try:
        r = _session.get(rp.url, timeout=TIMEOUT)
    finally:
        _mark_request()
    # Theo RFC 9309 mục 2.3.1: robots.txt trả 4xx (kể cả 401/403) = không có quy tắc;
    # 5xx hoặc lỗi mạng = phải coi như cấm toàn bộ, nên dừng hẳn.
    if 400 <= r.status_code < 500:
        rp.allow_all = True
    elif r.ok:
        rp.parse(r.text.splitlines())
    else:
        raise RuntimeError(f"[crawl] Không đọc được {rp.url} (HTTP {r.status_code}), dừng.")
    _robots[origin] = rp
    return rp


def is_allowed(url: str) -> bool:
    return _robots_for(url).can_fetch(_session.headers["User-Agent"], url)


@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    retry=retry_if_exception_type((Retryable, requests.ConnectionError, requests.Timeout)),
    reraise=True,
)
def _get_with_retry(url: str, referer: str | None, delay: tuple[float, float], stream: bool):
    _throttle(delay, url)
    headers = {"Referer": referer} if referer else {}
    try:
        r = _session.get(url, headers=headers, timeout=TIMEOUT, stream=stream)
    finally:
        _mark_request()
    if r.status_code == 403:
        print(f"[crawl] 403 Forbidden, dừng crawl (không vượt chặn): {url}")
        raise PermissionError(f"403 Forbidden: {url}")
    if r.status_code in RETRY_STATUS:
        print(f"[crawl] HTTP {r.status_code}, sẽ thử lại: {url}")
        raise Retryable(f"{r.status_code} {url}")
    r.raise_for_status()
    return r


def get(url: str, referer: str | None = None, *, kind: str = "page", stream: bool = False):
    """GET lịch sự. kind='pdf' dùng khoảng nghỉ dài hơn (5-10 giây)."""
    if not is_allowed(url):
        print(f"[crawl] robots.txt không cho phép, bỏ qua: {url}")
        raise RobotsDisallowed(url)
    delay = PDF_DELAY if kind == "pdf" else PAGE_DELAY
    return _get_with_retry(url, referer, delay, stream)
