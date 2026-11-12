"""Dò công thức mã 30 (LN thuần từ HĐKD) của B02 TT200 bằng số liệu, khi văn bản tham chiếu có công thức nghi sai.

Chạy thử từng công thức ứng viên trên mọi báo cáo TT200 đã qua critical B01 (B01 cân đối = OCR trang đó tin được hơn),
dùng chung bộ kiểm phương trình của pipeline (dấu khoản giảm trừ theo sign trong dim_account, sai số 1 đơn vị).
Ô in "-" tính là 0 như kiểm tổng dòng con. Công thức đúng ở > 90% báo cáo kiểm được thì được chọn; bằng chứng ghi vào
docs/dim_account_notes.md. Khi có bản TT202 chính thức trong docs/reference/ thì công thức in trong đó thắng.

    python tools/probe_b02_formula.py          # in kết quả + ghi bằng chứng
    python tools/probe_b02_formula.py --dry    # chỉ in
"""
import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.extraction.pdf_extractor import _terms, check_equations, load_dim_account, signs_of  # noqa: E402

RAW = ROOT / "data" / "silver" / "fs_extracted_raw"
NOTES = ROOT / "docs" / "dim_account_notes.md"
MARK_START, MARK_END = "<!-- probe-30:start -->", "<!-- probe-30:end -->"
THRESHOLD = 0.9
OPTIONAL = {"24"}  # MWG bỏ dòng 24 (lãi/lỗ liên doanh) ở 16/47 báo cáo TT200: không in = 0, như dq_equations.csv
CANDIDATES = {  # nguồn của từng ứng viên
    "20+21-22+24-25-26": "cấu trúc mẫu B02-DN/HN: 30 = 20 + (21 - 22) + 24 - (25 + 26)",
    "20+21-22-24-25": "công thức in trong bản TT202 của TVT-ProTrain: 30 = 20 + (21 - 22) - (24 + 25)",
    "20+21-22-25-26": "mẫu B02-DN riêng lẻ (không có mã 24): 30 = 20 + (21 - 22) - (25 + 26)",
}


def probe(raw: pl.DataFrame, signs: dict) -> dict[str, dict]:
    raw = raw.filter((pl.col("regime") == "TT200") & pl.col("passed_critical_b01") & (pl.col("statement") == "B02"))
    out = {rhs: {"ok": 0, "n": 0, "fails": []} for rhs in CANDIDATES}
    for (sha, period, lang), g in raw.group_by(["source_doc_sha256", "period", "lang"]):
        values = {("B02", c, k): (0 if v is None else v)  # ô in "-" là 0
                  for c, k, v in zip(g["account_code"], g["period_kind"], g["amount_vnd"])}
        for rhs in CANDIDATES:
            eq = {"regime": "TT200", "statement": "B02", "lhs": "30", "rhs": rhs, "terms": _terms(rhs),
                  "label": f"30 = {rhs}", "optional": OPTIONAL}
            res = [r for r in check_equations(values, [eq], "TT200", signs) if "thiếu" not in r["note"]]
            if not res:
                continue
            out[rhs]["n"] += 1
            if all(r["ok"] for r in res):
                out[rhs]["ok"] += 1
            else:
                out[rhs]["fails"].append(f"{period} {lang[:2]}")
    return out


def main(argv: list[str]) -> int:
    signs = signs_of(load_dim_account(ROOT / "config" / "dim_account.csv"), "TT200")
    result = probe(pl.read_delta(str(RAW)), signs)
    lines = [MARK_START, "", "## Dò công thức mã 30 (B02 TT200) bằng số liệu (tự sinh bởi `tools/probe_b02_formula.py`)", "",
             "Chạy trên các báo cáo TT200 đã qua critical B01; dòng 24 không in tính là 0; báo cáo thiếu vế khác "
             "(OCR mất mã) không tính. "
             f"Chọn công thức đúng ở > {THRESHOLD:.0%} báo cáo kiểm được.", "",
             "| Công thức 30 = | Nguồn ứng viên | Đúng / kiểm được | Tỷ lệ | Báo cáo trượt |", "|---|---|---|---|---|"]
    chosen = None
    for rhs, src in CANDIDATES.items():
        r = result[rhs]
        rate = r["ok"] / r["n"] if r["n"] else 0
        print(f"30 = {rhs}: {r['ok']}/{r['n']} ({rate:.0%})")
        lines.append(f"| {rhs} | {src} | {r['ok']}/{r['n']} | {rate:.0%} | {', '.join(sorted(r['fails'])[:8])} |")
        if rate > THRESHOLD and (chosen is None or rate > chosen[1]):
            chosen = (rhs, rate, r)
    lines += ["", f"**Chọn:** `30 = {chosen[0]}` ({chosen[2]['ok']}/{chosen[2]['n']})." if chosen
              else "**Không công thức nào đạt ngưỡng**: chưa thêm vào dq_equations.csv.", "", MARK_END]
    print("chọn:", chosen[0] if chosen else None)
    if "--dry" not in argv:
        text = NOTES.read_text(encoding="utf-8")
        block = "\n".join(lines)
        text = (text[:text.index(MARK_START)] + block + text[text.index(MARK_END) + len(MARK_END):]
                if MARK_START in text else text.rstrip() + "\n\n" + block + "\n")
        NOTES.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
