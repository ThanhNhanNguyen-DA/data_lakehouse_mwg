"""Bóc tách BCTC (TT200) từ "từ + tọa độ" của trang (OCR hoặc pdfplumber, xem ocr.py).

Bố cục quan sát trên PDF thật của MWG (vi-VN và en-US):
- BCTC năm: Mã số | Chỉ tiêu | Thuyết minh | Số cuối năm | Số đầu năm (B02: Năm nay | Năm trước).
- BCTC quý, B02: Chỉ tiêu | Mã số | Thuyết minh | Quý này năm nay | Lũy kế năm nay |
  Quý này năm trước | Lũy kế năm trước; mã in "1", "2" (không có số 0) và bên trái còn số thứ tự.
- Tên chỉ tiêu dài xuống dòng: mã số nằm ở dòng đầu, số tiền nằm ở dòng cuối.
- Số tiền trong ngoặc là số âm; các khoản giảm trừ (02, 11, ...) thường in âm.
Vì vậy: neo theo vị trí tiêu đề cột "Mã số"/"Code", gán kỳ cho cột theo tiêu đề cột,
và gắn số tiền vào mã gần nhất phía trên.
"""
from __future__ import annotations

import csv
import re
from datetime import date
from pathlib import Path
from statistics import median

import pdfplumber

from src.common.text import norm


def has_text_layer(path: str | Path, max_pages: int = 5, min_chars: int = 50) -> bool | None:
    """PDF có lớp text không (đọc được bằng pdfplumber) hay là bản scan cần OCR.

    True nếu một trong max_pages trang đầu có ít nhất min_chars ký tự text.
    None nếu không mở được file (PDF hỏng/mã hóa) -> cần xem tay.
    """
    try:
        with pdfplumber.open(path) as pdf:
            return any(len((page.extract_text() or "").strip()) >= min_chars
                       for page in pdf.pages[:max_pages])
    except Exception:
        return None

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


_LONE_UNITS = {"vnd", "dong", "vnd trieu", "trieu vnd", "trieu dong", "vnd million", "million vnd",
               "nghin dong", "ty dong"}


def unit_multiplier(text: str) -> int | None:
    """Hệ số quy đổi về VND từ dòng 'Đơn vị tính: ...' (hoặc dòng chỉ có 'VND') hoặc None."""
    for line in text.splitlines():
        n = norm(line)
        m = _UNIT_PREFIX.search(n)
        if m:
            unit = " ".join(n[m.end():].split()[:2])  # vd. "trieu dong", "vnd million"
        elif re.sub(r"[^a-z ]", "", n).strip() in _LONE_UNITS:
            unit = n  # MWG chỉ in "VND" phía trên bảng
        else:
            continue
        for pattern, factor in _SCALES:
            if pattern.search(unit):
                return factor
    return None


# ---------- Số tiền trên bảng (OCR) ----------

_GROUPED = re.compile(r"\d{1,3}(?:[.,]\d{3})+")
_DASHES = {"-", "–", "—"}
_NUMERIC_CHARS = re.compile(r"^[\d.,()\-–—]+$")


def _clean(text: str) -> str:
    return text.strip().strip("|").replace(" ", "")


def parse_amount(text: str) -> int | None:
    """Số tiền VND (số nguyên) từ một ô; hiểu cả '1.234.567' (vi) lẫn '1,234,567' (en).

    '(1.234)' và '-1.234' là số âm; '-' là null. Không đọc được -> ValueError.
    """
    t = _clean(text)
    neg = t.startswith("(")  # OCR đôi khi mất dấu đóng ngoặc
    t = t.strip("()")
    if t in _DASHES or t == "":
        return None
    if t[0] in "-−":
        neg, t = True, t[1:]
    if not (_GROUPED.fullmatch(t) or re.fullmatch(r"\d{1,3}", t)):
        raise ValueError(f"Không đọc được số tiền: {text!r}")
    v = int(re.sub(r"[.,]", "", t))
    return -v if neg else v


def _is_grouped_amount(text: str) -> bool:
    return bool(_GROUPED.fullmatch(_clean(text).strip("()").lstrip("-−")))


# ---------- Gom từ thành dòng, dòng thành bảng ----------

def group_lines(words: list[dict]) -> list[list[dict]]:
    """Gom từ thành dòng theo trục y (dùng line_key của Tesseract nếu có), mỗi dòng sắp theo x."""
    words = [w for w in words if _clean(w["text"])]
    if not words:
        return []
    if all("line_key" in w for w in words):
        groups: dict = {}
        for w in words:
            groups.setdefault(w["line_key"], []).append(w)
        lines = list(groups.values())
    else:
        h = median(w["y1"] - w["y0"] for w in words)
        lines, centers = [], []
        for w in sorted(words, key=lambda w: (w["y0"] + w["y1"]) / 2):
            yc = (w["y0"] + w["y1"]) / 2
            if lines and abs(yc - centers[-1]) <= 0.45 * h:
                lines[-1].append(w)
                centers[-1] = sum((x["y0"] + x["y1"]) / 2 for x in lines[-1]) / len(lines[-1])
            else:
                lines.append([w])
                centers.append(yc)
    lines = [sorted(line, key=lambda w: w["x0"]) for line in lines]
    return sorted(lines, key=lambda line: sum(w["y0"] for w in line) / len(line))


def _merge_numbers(line: list[dict], gap: float) -> list[dict]:
    """Nối các mảnh số OCR tách rời ('1.234.' + '567') khi chúng sát nhau."""
    out: list[dict] = []
    for w in line:
        prev = out[-1] if out else None
        if (prev and _NUMERIC_CHARS.match(_clean(prev["text"])) and _NUMERIC_CHARS.match(_clean(w["text"]))
                and w["x0"] - prev["x1"] <= gap):
            out[-1] = {**prev, "text": _clean(prev["text"]) + _clean(w["text"]), "x1": w["x1"],
                       "y1": max(prev["y1"], w["y1"])}
        else:
            out.append(dict(w))
    return out


def _xc(w: dict) -> float:
    return (w["x0"] + w["x1"]) / 2


def _find(words: list[dict], names: set[str]) -> dict | None:
    return next((w for w in words if norm(_clean(w["text"])).strip(".:") in names), None)


def _band(word: dict | None, pad: float) -> tuple[float, float] | None:
    return (word["x0"] - pad, word["x1"] + pad) if word else None


_CODE_TOKEN = re.compile(r"^\d{1,3}[a-z]?$")


def _code_of(text: str) -> str | None:
    t = norm(_clean(text)).strip(".:,")
    return t if _CODE_TOKEN.match(t) else None


def _cluster(values: list[float], gap: float) -> list[list[float]]:
    out: list[list[float]] = []
    for v in sorted(values):
        if out and v - out[-1][-1] <= gap:
            out[-1].append(v)
        else:
            out.append([v])
    return out


def _column_kind(text: str, statement: str) -> str | None:
    t = norm(text)
    if statement == "B01":
        if any(k in t for k in ("cuoi nam", "cuoi ky", "ending", "closing", "end of")):
            return "closing"
        if any(k in t for k in ("dau nam", "dau ky", "beginning", "opening")):
            return "opening"
        return None
    ytd = any(k in t for k in ("luy ke", "accumulated", "cumulative", "year to date", "year-to-date"))
    quarter = any(k in t for k in ("quy nay", "this quarter", "quarter"))
    if any(k in t for k in ("nam truoc", "previous", "prior", "last year", "preceding")):
        when = "prior"
    elif any(k in t for k in ("nam nay", "current", "this year")):
        when = "current"
    else:
        return None
    return ("ytd_" if ytd else "q_" if quarter else "") + when


# Thứ tự cột khi không đọc được tiêu đề cột (quan sát trên BCTC MWG)
_POSITIONAL_KINDS = {
    ("B01", 2): ["closing", "opening"],
    ("B02", 2): ["current", "prior"],
    ("B03", 2): ["current", "prior"],
    ("B02", 4): ["q_current", "ytd_current", "q_prior", "ytd_prior"],
}


def _years_kinds(texts: list[str], statement: str) -> list[str] | None:
    """Tiêu đề cột chỉ ghi ngày (vd. '30/6/2025' và '30/6/2024'): năm lớn hơn là kỳ này."""
    years = [max(map(int, re.findall(r"20\d{2}", t)), default=None) for t in texts]
    if len(years) != 2 or None in years or years[0] == years[1]:
        return None
    first = ("closing", "opening") if statement == "B01" else ("current", "prior")
    return list(first) if years[0] > years[1] else list(reversed(first))


def parse_statement_page(words: list[dict], statement: str, prev: dict | None = None) -> dict:
    """Bóc một trang báo cáo: trả về {rows, kinds, header_ok, code_band, note_band, header_text}.

    prev: kết quả trang trước của cùng báo cáo (dùng lại dải cột nếu trang này thiếu tiêu đề cột).
    """
    if not words:
        return {"rows": [], "kinds": [], "header_ok": False, "header_text": "", "code_band": None,
                "note_band": None}
    h = median(w["y1"] - w["y0"] for w in words)
    lines = [_merge_numbers(line, 0.6 * h) for line in group_lines(words)]
    first_amount = next((i for i, line in enumerate(lines) if any(_is_grouped_amount(w["text"]) for w in line)),
                        len(lines))
    header = [w for line in lines[:first_amount] for w in line]
    header_text = "\n".join(" ".join(w["text"] for w in line) for line in lines[:first_amount])

    code_word = _find(header, {"ma", "ma so", "maso", "code"})
    code_band = _band(code_word, pad=1.5 * h) or (prev or {}).get("code_band")
    note_band = _band(_find(header, {"thuyet", "thuyet minh", "notes", "note"}), pad=0.5 * h) \
        or (prev or {}).get("note_band")
    # Mã có thể nằm cao hơn dòng số tiền (B02 quý), nên tìm mã ở mọi dòng dưới chữ "Mã số"/"Code"
    first_row = next((i for i, line in enumerate(lines) if code_word and min(w["y0"] for w in line) > code_word["y1"]
                      and not any(w is code_word for w in line)), first_amount) if code_word else first_amount
    if code_band is None:  # không thấy tiêu đề cột: lấy cụm x chứa mã lớn nhất (số thứ tự chỉ 1..20)
        cands = [(w, int(re.sub(r"\D", "", c))) for line in lines[first_amount:] for w in line
                 if (c := _code_of(w["text"]))]
        if not cands:
            return {"rows": [], "kinds": [], "header_ok": False, "header_text": header_text,
                    "code_band": None, "note_band": note_band}
        clusters = _cluster([_xc(w) for w, _ in cands], 2 * h)
        best = max(clusters, key=lambda c: max(v for w, v in cands if _xc(w) in c))
        code_band = (min(best) - 1.5 * h, max(best) + 1.5 * h)
    left_limit = max(code_band[1], note_band[1] if note_band else code_band[1])

    body = [w for line in lines[first_amount:] for w in line]
    rights = [w["x1"] for w in body if _is_grouped_amount(w["text"]) and w["x0"] > left_limit - h]
    min_size = 2 if len(rights) >= 10 else 1  # trang nhiều số: bỏ cụm lẻ do OCR nhiễu
    cols = [median(c) for c in _cluster(rights, 2.5 * h) if len(c) >= min_size]
    if not cols:
        return {"rows": [], "kinds": [], "header_ok": False, "header_text": header_text,
                "code_band": code_band, "note_band": note_band}

    # Tiêu đề từng cột: chữ trong vùng tiêu đề nằm giữa mép phải cột trước và mép phải cột này
    bounds = [left_limit] + cols
    col_texts = [" ".join(w["text"] for w in header if bounds[i] - h < _xc(w) <= bounds[i + 1] + h)
                 for i in range(len(cols))]
    kinds = [_column_kind(t, statement) for t in col_texts]
    header_ok = None not in kinds and len(set(kinds)) == len(kinds)
    if not header_ok:
        kinds = _years_kinds(col_texts, statement) or _POSITIONAL_KINDS.get((statement, len(cols))) \
            or [f"col_{i + 1}" for i in range(len(cols))]
        if prev and prev.get("kinds") and len(prev["kinds"]) == len(cols):
            kinds = prev["kinds"]  # trang tiếp theo thường không lặp tiêu đề cột

    def values_of(line):
        vals, found = [None] * len(cols), False
        for w in line:
            t = _clean(w["text"])
            if w["x0"] <= left_limit - h or not (_is_grouped_amount(t) or t.strip("()") in _DASHES
                                                   or re.fullmatch(r"\(?\d{1,3}\)?", t)):
                continue
            i = min(range(len(cols)), key=lambda j: abs(w["x1"] - cols[j]))
            if abs(w["x1"] - cols[i]) <= 2 * h:
                try:
                    vals[i], found = parse_amount(t), True
                except ValueError:
                    vals[i], found = None, True
        return vals, found

    rows, anchors = [], []
    for i in range(min(first_row, first_amount), len(lines)):
        code = next((c for w in lines[i] if code_band[0] <= _xc(w) <= code_band[1] and (c := _code_of(w["text"]))),
                    None)
        if code:
            anchors.append((i, code))
    for n, (i, code) in enumerate(anchors):
        end = anchors[n + 1][0] if n + 1 < len(anchors) else len(lines)
        values, raw, name = [None] * len(cols), "", []
        for line in lines[i:end]:
            name += [w["text"] for w in line if w["x1"] < (note_band[0] if note_band else cols[0] - 4 * h)
                     and not (code_band[0] <= _xc(w) <= code_band[1])]
            vals, found = values_of(line)
            if found:
                values, raw = vals, " ".join(w["text"] for w in line if w["x0"] > left_limit - h)
                break
        if statement in ("B02", "B03") and len(code) == 1:
            code = code.zfill(2)  # BCTC quý in "1", "2"
        rows.append({"account_code": code, "item_name": " ".join(name).strip(), "values": values, "raw": raw})
    return {"rows": rows, "kinds": kinds, "header_ok": header_ok, "header_text": header_text,
            "code_band": code_band, "note_band": note_band}


# ---------- Tìm trang báo cáo ----------

STATEMENT_TITLES = {
    "B01": ("bang can doi ke toan", "balance sheet", "statement of financial position"),
    "B02": ("ket qua hoat dong kinh doanh", "income statement", "statement of income"),
    "B03": ("luu chuyen tien te", "cash flow"),
}
_FORM_CODE = re.compile(r"\bb\s*0?([1-39])\s*a?\s*[-–]\s*dn")
TOP_LINES = 15


def classify_page(text: str) -> dict:
    """Trang này là báo cáo nào (theo mã mẫu 'B01-DN/HN' hoặc tiêu đề), có phải thuyết minh không."""
    lines = [norm(x) for x in text.splitlines() if x.strip()]
    top, body = " ".join(lines[:TOP_LINES]), " ".join(lines)
    form = _FORM_CODE.search(top)
    titles = {st for st, keys in STATEMENT_TITLES.items() if any(k in top for k in keys)}
    is_notes = (form is not None and form.group(1) == "9") or "thuyet minh bao cao" in top or "notes to the" in top
    has_table = sum(1 for t in body.split() if re.fullmatch(r"\d{2,3}[a-z]?", t)) >= 5
    statement = None
    if form and form.group(1) in "123":
        statement = f"B0{form.group(1)}"
    elif len(titles) == 1 and has_table and not is_notes:
        statement = titles.pop()
    return {"statement": statement, "is_notes": is_notes and statement is None, "has_table": has_table}


def locate_statement_pages(get_text, n_pages: int, max_pages: int = 40) -> dict[str, list[int]]:
    """Duyệt trang từ đầu, dừng khi đã qua phần báo cáo và gặp thuyết minh."""
    pages: dict[str, list[int]] = {"B01": [], "B02": [], "B03": []}
    current = None
    for p in range(1, min(n_pages, max_pages) + 1):
        info = classify_page(get_text(p))
        st = info["statement"] or (current if info["has_table"] and not info["is_notes"] else None)
        if st:
            pages[st].append(p)
            current = st
            continue
        current = None
        if info["is_notes"] and any(pages.values()):
            break
    return pages


# ---------- Ngày báo cáo ----------

_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
           "october", "november", "december"]
_VI_DATE = re.compile(r"ngay\s*(\d{1,2})\s*thang\s*(\d{1,2})\s*nam\s*(\d{4})")
_NUM_DATE = re.compile(r"\b(\d{1,2})\s*[/.-]\s*(\d{1,2})\s*[/.-]\s*(\d{4})\b")
_EN_DATE = re.compile(rf"\b(\d{{1,2}})\s+({'|'.join(_MONTHS)})\s*,?\s*(\d{{4}})\b")
_EN_DATE_US = re.compile(rf"\b({'|'.join(_MONTHS)})\s+(\d{{1,2}})\s*,\s*(\d{{4}})\b")
QUARTER_ENDS = {(3, 31), (6, 30), (9, 30), (12, 31)}


def find_dates(text: str) -> list[date]:
    t = norm(text)
    found = []
    for d, m, y in _VI_DATE.findall(t) + _NUM_DATE.findall(t):
        found.append((int(y), int(m), int(d)))
    found += [(int(y), _MONTHS.index(m) + 1, int(d)) for d, m, y in _EN_DATE.findall(t)]
    found += [(int(y), _MONTHS.index(m) + 1, int(d)) for m, d, y in _EN_DATE_US.findall(t)]
    out = []
    for y, m, d in found:
        try:
            out.append(date(y, m, d))
        except ValueError:
            pass
    return out


def report_end_date(text: str) -> date | None:
    """Ngày kết thúc kỳ: ngày cuối quý muộn nhất in trong phần đầu báo cáo (bỏ ngày ký)."""
    ends = [d for d in find_dates(text) if (d.month, d.day) in QUARTER_ENDS]
    return max(ends, default=None)


def expected_end(period: str) -> date | None:
    m = re.fullmatch(r"(\d{4})(?:-(Q[1-4]|H1))?", period or "")
    if not m:
        return None
    y, part = int(m.group(1)), m.group(2)
    month = {"Q1": 3, "Q2": 6, "H1": 6, "Q3": 9, "Q4": 12, None: 12}[part]
    return date(y, month, {3: 31, 6: 30, 9: 30, 12: 31}[month])


# ---------- Kiểm tra DQ ----------

# (báo cáo, mã vế trái, [(dấu, mã)]). Khoản giảm trừ (02, 11) MWG in số âm trong ngoặc,
# nên vế trừ dùng giá trị tuyệt đối: 10 = 01 - |02|, 20 = 10 - |11|.
EQUATIONS = [
    ("B01", "270", [(1, "300"), (1, "400")], "270 = 300 + 400"),
    ("B02", "10", [(1, "01"), (-1, "02")], "10 = 01 - 02"),
    ("B02", "20", [(1, "10"), (-1, "11")], "20 = 10 - 11"),
]


def check_equations(values: dict[tuple[str, str, str], int | None], tol: int = 1) -> list[dict]:
    """values: {(báo cáo, mã, kỳ cột): số}. Mỗi phương trình kiểm trên mọi cột có vế trái."""
    out = []
    for st, lhs, terms, label in EQUATIONS:
        kinds = sorted({k for (s, c, k) in values if s == st and c == lhs})
        if not kinds:
            out.append({"check": label, "kind": None, "ok": False, "note": f"thiếu mã {lhs}"})
            continue
        for kind in kinds:
            left = values.get((st, lhs, kind))
            parts = [values.get((st, code, kind)) for _, code in terms]
            if left is None or None in parts:
                missing = [c for (_, c), v in zip(terms, parts) if v is None] + ([lhs] if left is None else [])
                out.append({"check": label, "kind": kind, "ok": False, "note": f"thiếu {', '.join(missing)}"})
                continue
            right = sum(v if sign > 0 else -abs(v) for (sign, _), v in zip(terms, parts))
            out.append({"check": label, "kind": kind, "ok": abs(left - right) <= tol,
                        "note": "" if abs(left - right) <= tol else f"lệch {left - right:,}"})
    return out


# ---------- Sửa tay (config/manual_overrides.csv) ----------

def load_overrides(path: Path) -> dict[tuple[str, str, str, str], dict]:
    if not Path(path).exists():
        return {}
    with Path(path).open(encoding="utf-8", newline="") as f:
        return {(r["source_doc_sha256"], r["statement"], r["account_code"], r["period_kind"]): r
                for r in csv.DictReader(f) if r.get("amount_vnd", "").strip()}


def apply_overrides(rows: list[dict], overrides: dict) -> list[dict]:
    """Thay giá trị bóc tự động của đúng ô (sha256, báo cáo, mã, kỳ cột) TRƯỚC bước chọn bản thắng."""
    out = []
    for r in rows:
        o = overrides.get((r["source_doc_sha256"], r["statement"], r["account_code"], r["period_kind"]))
        out.append({**r, "amount_vnd": int(o["amount_vnd"]), "is_manual": True} if o else {**r, "is_manual": False})
    return out
