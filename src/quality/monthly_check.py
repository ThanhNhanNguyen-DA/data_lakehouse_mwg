"""Đối soát B02 mã 10 (doanh thu thuần) và 60 (LNST) với báo cáo KQKD tháng lũy kế tháng 3/6/9/12 của MWG.

Thay cho vnstock (gói không còn trên PyPI). Báo cáo tháng là bản tóm tắt dạng infographic/văn xuôi, số tính bằng
tỷ đồng, bố cục đổi theo năm, nên chỉ kiểm tra **có mặt**: số BCTC làm tròn về tỷ đồng (±1) có xuất hiện trong chữ
của báo cáo tháng cùng kỳ lũy kế không. Khớp = xác nhận; không thấy != sai (có thể báo cáo tháng không in số đó,
hoặc in LNST của cổ đông công ty mẹ - nên thử cả mã 61). Chỉ ghi vào docs/dq_report.md, không ghi vào Gold.
"""
from __future__ import annotations

import re

from src.common.paths import ROOT
from src.extraction import ocr

CODES = ("10", "60", "61")
TOL_BILLION = 1  # làm tròn về tỷ đồng ở báo cáo tháng
_NUM = re.compile(r"\d{1,3}(?:[.,]\d{3})+|\d+")


def numbers_in(text: str) -> set[int]:
    """Mọi số nguyên trong chữ (bỏ dấu phân cách nghìn '.' hoặc ',')."""
    return {int(re.sub(r"[.,]", "", m)) for m in _NUM.findall(text)}


def one_digit_off(a: int, b: int) -> bool:
    """Cùng độ dài (>= 4 chữ số), sai đúng một chữ số: dấu hiệu OCR; số ngắn thì lệch nhỏ cũng hay trùng ngẫu nhiên."""
    sa, sb = str(abs(a)), str(abs(b))
    return len(sa) == len(sb) >= 4 and sum(x != y for x, y in zip(sa, sb)) == 1


def nearest(billion: int, numbers: set[int]) -> int | None:
    """Số cùng số chữ số gần nhất trong báo cáo tháng (để người dùng thấy lệch một chữ số hay lệch thật)."""
    same = [n for n in numbers if len(str(n)) == len(str(abs(billion)))]
    return min(same, key=lambda n: abs(n - billion)) if same else None


def month_of(period: str, kind: str) -> str | None:
    """Kỳ lũy kế (YYYY-Mmm) mà cột B02 này trình bày, nếu là lũy kế tháng 3/6/9/12 của năm hiện tại."""
    year, rest = period[:4], period[5:]
    if not year.isdigit():
        return None
    if rest == "" and kind == "current":
        return f"{year}-M12"
    if rest == "H1" and kind == "current":
        return f"{year}-M06"
    month = {"Q1": "03", "Q2": "06", "Q3": "09", "Q4": "12"}.get(rest)
    if month and (kind == "ytd_current" or (rest == "Q1" and kind == "q_current")):
        return f"{year}-M{month}"
    return None


def _text(row: dict) -> str:
    path = ROOT / row["path"]
    return "\n".join(ocr.page_text(path, row["sha256"], p, bool(row["has_text_layer"]))
                     for p in range(1, ocr.page_count(path) + 1))


def monthly_crosscheck(docs: list[dict], mo_rows: list[dict]) -> list[dict]:
    """Mỗi ô B02 (mã 10/60/61) của BCTC ứng với lũy kế tháng 3/6/9/12: có thấy trong báo cáo tháng không."""
    monthly = {r["period"]: r for r in mo_rows if re.search(r"-M(03|06|09|12)$", r["period"] or "")}
    texts: dict[str, set[int] | None] = {}
    out = []
    for d in docs:
        for c in d["cells"]:
            code = c.get("canonical_code")
            if c["statement"] != "B02" or code not in CODES or c["amount_vnd"] is None:
                continue
            month = month_of(d["period"], c["period_kind"])
            if month not in monthly:
                continue
            if month not in texts:
                try:
                    texts[month] = numbers_in(_text(monthly[month]))
                except Exception:  # file lỗi: bỏ qua kỳ đó, không chặn pipeline
                    texts[month] = None
            if texts[month] is None:
                continue
            billion = round(c["amount_vnd"] / 1e9)
            out.append({"month": month, "period": d["period"], "lang": d["lang"], "code": code, "billion": billion,
                        "found": any(abs(billion - n) <= TOL_BILLION for n in texts[month]),
                        "nearest": nearest(billion, texts[month]), "gold_eligible": c.get("gold_eligible", False),
                        "doc": d, "cell": c})
    return out


def report_lines(rows: list[dict]) -> list[str]:
    if not rows:
        return ["Chưa có kỳ nào đối soát được.", ""]
    lines = []
    for code in ("10", "60"):
        rs = [r for r in rows if r["code"] == code]
        ok = [r for r in rs if r["found"]]
        rescued = [r for r in ok if not r["gold_eligible"]]
        lines.append(f"- Mã {code}: thấy trong báo cáo tháng {len(ok)}/{len(rs)} ô; trong đó {len(rescued)} ô chưa "
                     "vào được Gold (báo cáo tháng xác nhận thêm, làm tròn tỷ đồng).")
    missing = sorted({(r["period"], r["lang"], r["month"]) for r in rows if r["code"] in ("10", "60") and not r["found"]
                      and not any(x["found"] for x in rows if x["period"] == r["period"] and x["code"] == "61"
                                  and r["code"] == "60")})
    lines += ["", "| Kỳ BCTC | Lang | Báo cáo tháng | Mã | BCTC (tỷ đồng) | Thấy trong báo cáo tháng | "
              "Số cùng độ dài gần nhất | Ô vào Gold |", "|---|---|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: (r["month"], r["period"], r["code"])):
        near = r.get("nearest")
        hint = "" if r["found"] or near is None else (
            f"{near:,}" + (" (lệch một chữ số: nghi OCR)" if one_digit_off(near, r["billion"]) else ""))
        if r.get("reread") is not None:
            hint += f"; đọc lại PDF: {r['reread']:,} → pending"
        elif "reread" in r:
            again = r.get("reread_any")
            hint += ("; đọc lại PDF không ra số nào" if again is None else
                     f"; đọc lại PDF vẫn ra {round(abs(again) / 1e9):,} tỷ: nghi báo cáo tháng (sơ bộ/OCR)")
        lines.append(f"| {r['period']} | {r['lang'][:2]} | {r['month']} | {r['code']} | {r['billion']:,} | "
                     f"{'✓' if r['found'] else '–'} | {hint} | {'✓' if r['gold_eligible'] else '–'} |")
    lines += ["", f"Kỳ có mã 10/60 không thấy trong báo cáo tháng: {len(missing)}. '–' không có nghĩa là sai: báo cáo "
              "tháng có thể không in số đó (vd. báo cáo 2024–2026 chỉ in doanh thu).", ""]
    return lines
