"""Map mã TT99 -> mã TT200 bằng số liệu ngày 31/12/2025 (chạy sau tools/build_dim_account.py).

So cột đầu năm B01 của các BCTC năm 2026 (TT99, ngày 31/12/2025) với cột cuối kỳ B01 của BCTC Q4/2025
(TT200, cùng ngày). Một mã TT99 khớp đúng một mã TT200 (|chênh| <= 1 VND) thì ghép với
mapping_basis = numeric_31122025; khớp nhiều mã, không khớp, hay mâu thuẫn với ghép theo tên thì liệt kê.

    python tools/map_tt99_numeric.py                 # so với BCTC Q4/2025 (mặc định)
    python tools/map_tt99_numeric.py --ref 2025 --dry  # thử so với BCTC năm 2025 đã kiểm toán, không ghi file

Chỉ ghép mã cùng cấp (level trong dim_account): dòng tổng bằng dòng con duy nhất của nó (vd. 330 = 336 khi
chỉ có một khoản nợ dài hạn) không được ghép nhầm.
"""
import csv
import sys
from collections import Counter
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / "config" / "account_code_regime_map.csv"
NOTES = ROOT / "docs" / "dim_account_notes.md"
RAW = ROOT / "data" / "silver" / "fs_extracted_raw"
BASIS = "numeric_31122025"
FIELDS = ["regime", "statement", "account_code", "canonical_code", "mapping_basis", "source", "note"]
MARK_START, MARK_END = "<!-- numeric-map:start -->", "<!-- numeric-map:end -->"


def main(argv: list[str]) -> int:
    ref = argv[argv.index("--ref") + 1] if "--ref" in argv else "2025-Q4"
    dry = "--dry" in argv
    dim = list(csv.DictReader((ROOT / "config" / "dim_account.csv").open(encoding="utf-8")))
    level = {(r["regime"], r["account_code"]): r["level"] for r in dim if r["statement"] == "B01"}
    raw = pl.read_delta(str(RAW)).filter(pl.col("statement") == "B01")
    tt99 = raw.filter((pl.col("regime") == "TT99") & (pl.col("period").str.starts_with("2026"))
                      & (pl.col("period_kind") == "opening") & pl.col("amount_vnd").is_not_null())
    q4 = raw.filter((pl.col("period") == ref) & (pl.col("lang") == "vi-VN") & (pl.col("period_kind") == "closing")
                    & pl.col("amount_vnd").is_not_null())
    # mỗi mã TT99: giá trị đa số giữa các báo cáo 2026 (giảm ảnh hưởng của một ô OCR sai)
    v99 = {code: Counter(g["amount_vnd"].to_list()).most_common(1)[0][0]
           for (code,), g in tt99.group_by(["account_code"])}
    v200 = dict(zip(q4["account_code"], q4["amount_vnd"]))

    rows = list(csv.DictReader(MAP.open(encoding="utf-8")))
    result = {"mới ghép": [], "khớp với ghép theo tên": [], "mâu thuẫn với ghép theo tên": [],
              "khớp nhiều mã": [], "không khớp": [], "bằng 0": []}
    for r in rows:
        r.setdefault("mapping_basis", "")
        if r["canonical_code"] and not r["mapping_basis"]:
            r["mapping_basis"] = "name"
        if r["statement"] != "B01" or r["account_code"] not in v99:
            continue
        v = v99[r["account_code"]]
        if v == 0:
            result["bằng 0"].append((r["account_code"], v, []))
            continue
        hits = sorted(c for c, x in v200.items() if abs(x - v) <= 1
                      and level.get(("TT200", c)) == level.get(("TT99", r["account_code"])))
        item = (r["account_code"], v, hits)
        if len(hits) == 1:
            if not r["canonical_code"]:
                r.update(canonical_code=hits[0], mapping_basis=BASIS,
                         source=f"số liệu 31/12/2025: đầu năm BCTC 2026 = cuối kỳ BCTC {ref}, cùng cấp", note="")
                result["mới ghép"].append(item)
            elif r["canonical_code"] == hits[0]:
                r["mapping_basis"] = "name+" + BASIS
                result["khớp với ghép theo tên"].append(item)
            else:
                r["note"] = f"số liệu 31/12/2025 khớp mã {hits[0]}, khác ghép theo tên ({r['canonical_code']}): cần xác nhận"
                result["mâu thuẫn với ghép theo tên"].append((r["account_code"], v, [r["canonical_code"], *hits]))
        elif hits:
            result["khớp nhiều mã"].append(item)
        else:
            result["không khớp"].append(item)
    for k, items in result.items():
        print(f"{k}: {len(items)}", [(c, h) for c, _, h in items])
    if dry:
        return 0
    with MAP.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows({k: r.get(k, "") for k in FIELDS} for r in rows)

    lines = [MARK_START, "", "## Map bằng số liệu ngày 31/12/2025 (tự sinh bởi `tools/map_tt99_numeric.py`)", "",
             f"So cột đầu năm B01 của BCTC 2026 (TT99) với cột cuối kỳ B01 của BCTC {ref} bản vi-VN (TT200), "
             "chỉ giữa các mã cùng cấp. Khớp đúng một mã thì ghép (`mapping_basis = numeric_31122025`).", "",
             "| Kết quả | Số mã | Mã TT99 → mã TT200 khớp số |", "|---|---|---|"]
    for k, items in result.items():
        shown = ", ".join(f"{c} → {'/'.join(h) or '–'}" for c, _, h in items)
        lines.append(f"| {k} | {len(items)} | {shown} |")
    lines += ["", MARK_END]
    text = NOTES.read_text(encoding="utf-8")
    block = "\n".join(lines)
    text = (text[:text.index(MARK_START)] + block + text[text.index(MARK_END) + len(MARK_END):]
            if MARK_START in text else text.rstrip() + "\n\n" + block + "\n")
    NOTES.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
