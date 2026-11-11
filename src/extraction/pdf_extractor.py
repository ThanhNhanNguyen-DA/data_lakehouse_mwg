"""Bóc tách BCTC (TT200) từ PDF bằng pdfplumber.

Tuần 1 mới có phần đọc số tiền và đơn vị tính. Hàm extract() (neo theo cột "Mã số")
và kiểm tra phương trình kế toán viết ở tuần 4-5.
"""
from __future__ import annotations

import re

from src.common.text import norm

_NULL_TOKENS = {"", "-", "–", "—"}  # rỗng, '-', en dash, em dash
_MINUS = ("-", "−")  # dấu trừ ASCII và dấu trừ Unicode


def to_number(s: str | None, thousands: str = ".", decimal: str = ",") -> float | None:
    """Chuyển số tiền trên BCTC sang float.

    Mặc định kiểu Việt Nam: '1.234.567' -> 1234567.0, '(1.234)' -> -1234.0, '12,5' -> 12.5.
    '-', rỗng hoặc None -> None. BCTC tiếng Anh: gọi với thousands=',', decimal='.'.
    Chuỗi không đúng định dạng -> ValueError (để lỗi bóc tách lộ ra thay vì ra số sai).
    """
    if s is None:
        return None
    t = s.strip().replace(" ", "").replace(" ", "")
    neg = False
    if t.startswith("(") and t.endswith(")"):
        neg, t = True, t[1:-1]
    if t in _NULL_TOKENS:
        return None
    if t.startswith(_MINUS):
        neg, t = True, t[1:]

    th, dec = re.escape(thousands), re.escape(decimal)
    if not re.fullmatch(rf"(?:\d{{1,3}}(?:{th}\d{{3}})+|\d+)(?:{dec}\d+)?", t):
        raise ValueError(f"Không đọc được số tiền: {s!r}")
    v = float(t.replace(thousands, "").replace(decimal, "."))
    return -v if neg else v


_UNIT_PREFIX = re.compile(r"(?:don vi tinh|don vi)\s*:?\s*|unit\s*:\s*")
_SCALES = [
    (re.compile(r"\b(?:ty|billion)\b"), 1_000_000_000),
    (re.compile(r"\b(?:trieu|million)\b"), 1_000_000),
    (re.compile(r"\b(?:nghin|ngan|thousand)\b"), 1_000),
    (re.compile(r"\b(?:vnd|dong)\b"), 1),
]


def unit_multiplier(text: str) -> int | None:
    """Hệ số quy đổi về VND từ dòng 'Đơn vị tính: ...' trong text của trang, hoặc None."""
    for line in text.splitlines():
        n = norm(line)
        m = _UNIT_PREFIX.search(n)
        if not m:
            continue
        unit = " ".join(n[m.end():].split()[:2])  # vd. "trieu dong", "vnd million"
        for pattern, factor in _SCALES:
            if pattern.search(unit):
                return factor
    return None
