"""Crawler tài liệu Quan hệ nhà đầu tư của MWG.

Tuần 1 mới có: đọc config, kiểm tra selector đã điền chưa, nhận diện loại tài liệu và kỳ.
Vòng lặp crawl + manifest viết ở tuần 2-3, sau khi sources.yaml có selector thật.
"""
from __future__ import annotations

import re
import sys
from functools import lru_cache
from pathlib import Path

import yaml

from src.common.paths import CONFIG_DIR
from src.common.text import norm

SOURCES_PATH = CONFIG_DIR / "sources.yaml"
_PLACEHOLDER = re.compile(r"<[^>]*>")


@lru_cache(maxsize=None)
def load_config(path: Path = SOURCES_PATH) -> dict:
    with Path(path).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def unfilled_fields(cfg: dict) -> list[str]:
    """Các trường trong listing_pages còn để placeholder <...> (chưa khảo sát)."""
    pages = cfg.get("listing_pages") or []
    if not pages:
        return ["listing_pages"]
    return [
        f"listing_pages[{i}].{key}"
        for i, page in enumerate(pages)
        for key, value in page.items()
        if isinstance(value, str) and _PLACEHOLDER.search(value)
    ]


# ---------- Nhận diện loại tài liệu ----------

def _has_word(text: str, keyword: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", text) is not None


def classify(title: str, rules: list[dict] | None = None) -> str | None:
    """Trả về doc_type theo quy tắc đầu tiên khớp trong doc_rules, hoặc None."""
    if rules is None:
        rules = load_config()["doc_rules"]
    t = norm(title)
    for rule in rules:
        all_kw, any_kw, none_kw = rule.get("all", []), rule.get("any", []), rule.get("none", [])
        if not all_kw and not any_kw:
            raise ValueError(f"Quy tắc thiếu từ khóa all/any: {rule}")
        if (all(_has_word(t, k) for k in all_kw)
                and (not any_kw or any(_has_word(t, k) for k in any_kw))
                and not any(_has_word(t, k) for k in none_kw)):
            return rule["doc_type"]
    return None


# ---------- Nhận diện kỳ báo cáo ----------

_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4}
_YEAR = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
_QUARTER = re.compile(r"\b(?:quy|quarter|q)\s*([1-4]|iv|iii|ii|i)\b")
_MONTH = re.compile(r"\b(1[0-2]|[1-9])\s*(?:thang|months?)\b|\bthang\s*(1[0-2]|[1-9])\b")
_HALF_HINTS = ("soat xet", "ban nien", "giua nien do", "6 thang", "sau thang",
               "semi-annual", "half-year", "half year", "6 months", "six months")


def parse_period(title: str, doc_type: str | None = None) -> str | None:
    """Kỳ báo cáo từ tiêu đề: '2026-Q3', '2026-H1', '2026' (năm) hoặc '2026-M10' (báo cáo tháng).

    Trả về None nếu không xác định được, để crawler ghi 'unknown' thay vì đoán.
    """
    t = norm(title)
    y = _YEAR.search(t)
    if not y:
        return None
    year = y.group(1)

    if doc_type == "FS_CONSOLIDATED_Y":
        return year
    if doc_type == "FS_CONSOLIDATED_H":
        return f"{year}-H1"
    if doc_type == "MONTHLY_RESULT":
        m = _MONTH.search(t)
        return f"{year}-M{int(m.group(1) or m.group(2)):02d}" if m else None

    q = _QUARTER.search(t)
    if q:
        val = q.group(1)
        return f"{year}-Q{_ROMAN.get(val) or int(val)}"
    if doc_type == "FS_CONSOLIDATED_Q":
        return None  # loại quý mà không thấy số quý: không đoán
    if any(h in t for h in _HALF_HINTS):
        return f"{year}-H1"
    return year


def main() -> int:
    cfg = load_config()
    missing = unfilled_fields(cfg)
    if missing:
        print("[crawl] sources.yaml chưa được điền sau khảo sát, dừng lại:")
        for field in missing:
            print(f"[crawl]   - {field}")
        print("[crawl] Hãy mở trang IR trên mwg.vn bằng DevTools và điền URL/selector thật.")
        return 1
    print("[crawl] Vòng lặp crawl chưa được viết (tuần 2-3).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
