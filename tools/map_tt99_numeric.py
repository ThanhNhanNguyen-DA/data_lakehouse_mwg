"""Map mã TT99 -> mã TT200 bằng số liệu ngày 31/12/2025 (chạy sau tools/build_dim_account.py).

So cột đầu năm B01 của các BCTC năm 2026 (TT99, ngày 31/12/2025) với cột cuối kỳ B01 của BCTC Q4/2025
(TT200, cùng ngày). Một mã TT99 khớp đúng một mã TT200 (|chênh| <= 1 VND) thì ghép với
mapping_basis = numeric_31122025; khớp nhiều mã, không khớp, hay mâu thuẫn với ghép theo tên thì liệt kê.

    python tools/map_tt99_numeric.py                 # so với BCTC Q4/2025 (mặc định)
    python tools/map_tt99_numeric.py --ref 2025 --dry  # thử so với BCTC năm 2025 đã kiểm toán, không ghi file

Chỉ ghép mã cùng cấp (level trong dim_account): dòng tổng bằng dòng con duy nhất của nó (vd. 330 = 336 khi
chỉ có một khoản nợ dài hạn) không được ghép nhầm.

semantic_break = true: mã ghép theo tên nhưng số 31/12/2025 của MỌI báo cáo 2026 lệch số TT200 và không giống lỗi
OCR (khác số chữ số hoặc sai > 2 chữ số) -> cùng tên nhưng khác nội dung (vd. TT99 130 gồm khác TT200 130). Kỳ trước/sau mốc chuyển
chế độ không được so chéo (đầu năm 2026 vs cuối năm 2025) và KPI không nối chuỗi qua mã này.

Mỗi lần chạy tính lại từ đầu phần do số liệu sinh ra (ghép numeric, semantic_break, ghi chú), giữ ghép theo tên.
"""
import csv
import sys
from collections import Counter
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.extraction.self_correct import ocr_like  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / "config" / "account_code_regime_map.csv"
NOTES = ROOT / "docs" / "dim_account_notes.md"
RAW = ROOT / "data" / "silver" / "fs_extracted_raw"
BASIS = "numeric_31122025"
FIELDS = ["regime", "statement", "account_code", "canonical_code", "mapping_basis", "semantic_break", "source", "note"]
MARK_START, MARK_END = "<!-- numeric-map:start -->", "<!-- numeric-map:end -->"


SHIFT_LIMIT = 0.2  # mã đã ghép mà số 31/12/2025 hai bên lệch > 20%: nghi đổi nội dung, người dùng duyệt
SHIFT_KEY = "biến động > 20%"


def match_numeric(rows: list[dict], v99: dict, all99: dict, v200: dict, level: dict, ref: str,
                  v200_fallback: dict | None = None) -> dict:
    """Ghép mã TT99 -> TT200 bằng số 31/12/2025 (sửa rows tại chỗ). v99: số đa số mỗi mã TT99; all99: mọi số
    đọc được của mã đó; v200: số TT200 cùng ngày; level: {(regime, mã): cấp}. Trả {kết quả: [(mã, số, mã khớp)]}.
    v200_fallback: số TT200 cùng ngày từ tài liệu khác (BCTC Q4/2025) khi tài liệu mốc không đọc được ô đó: chỉ
    dùng cho semantic_break và danh sách biến động, không dùng để ghép mới."""
    fallback = v200_fallback or {}
    result = {"mới ghép": [], "khớp với ghép theo tên": [], "mâu thuẫn với ghép theo tên": [],
              "khớp nhiều mã": [], "không khớp": [], "bằng 0": [], "semantic_break": [],
              "báo cáo 2026 không thống nhất": [], SHIFT_KEY: []}
    for r in rows:
        basis = r.get("mapping_basis") or ""
        if basis == BASIS:  # ghép do lần chạy trước sinh ra: tính lại
            r.update(canonical_code="", source="")
        r["mapping_basis"] = "name" if r["canonical_code"] else ""
        r["semantic_break"] = ""
        if (r.get("note") or "").startswith("số liệu 31/12/2025"):
            r["note"] = ""
        if r["statement"] != "B01" or r["account_code"] not in v99:
            continue
        v = v99[r["account_code"]]
        if len(all99[r["account_code"]]) > 1:
            result["báo cáo 2026 không thống nhất"].append(
                (r["account_code"], v, [f"{x:,}" for x in sorted(all99[r["account_code"]])]))
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
            ref_v = (v200.get(r["canonical_code"], fallback.get(r["canonical_code"]))
                     if r["canonical_code"] else None)
            if ref_v is not None and all(abs(x - ref_v) > 1 and not ocr_like(x, ref_v) for x in all99[r["account_code"]]):
                r["semantic_break"] = "true"
                r["note"] = (f"số liệu 31/12/2025: TT99 {r['account_code']} = {v:,} khác TT200 {r['canonical_code']} = "
                             f"{ref_v:,}, không giống lỗi OCR: cùng tên nhưng khác nội dung")
                result["semantic_break"].append((r["account_code"], v, [r["canonical_code"]]))
        ref_v = v200.get(r["canonical_code"], fallback.get(r["canonical_code"])) if r["canonical_code"] else None
        if ref_v and abs(v - ref_v) > SHIFT_LIMIT * abs(ref_v):
            result[SHIFT_KEY].append((r["account_code"], v, [r["canonical_code"], ref_v, r["semantic_break"] == "true"]))
    return result


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
    all99 = {code: set(g["amount_vnd"].to_list()) for (code,), g in tt99.group_by(["account_code"])}
    v200 = dict(zip(q4["account_code"], q4["amount_vnd"]))
    alt = raw.filter((pl.col("period") == ("2025-Q4" if ref == "2025" else "2025")) & (pl.col("lang") == "vi-VN")
                     & (pl.col("period_kind") == "closing") & pl.col("amount_vnd").is_not_null())
    v200_fallback = dict(zip(alt["account_code"], alt["amount_vnd"]))

    rows = list(csv.DictReader(MAP.open(encoding="utf-8")))
    result = match_numeric(rows, v99, all99, v200, level, ref, v200_fallback)
    shifts = result.pop(SHIFT_KEY)
    for k, items in result.items():
        print(f"{k}: {len(items)}", [(c, h) for c, _, h in items])
    print(f"{SHIFT_KEY}: {len(shifts)}", [(c, h[0]) for c, _, h in shifts])
    if dry:
        return 0
    with MAP.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows({k: r.get(k, "") for k in FIELDS} for r in rows)

    lines = [MARK_START, "", "## Map bằng số liệu ngày 31/12/2025 (tự sinh bởi `tools/map_tt99_numeric.py`)", "",
             f"So cột đầu năm B01 của BCTC 2026 (TT99) với cột cuối kỳ B01 của BCTC {ref} bản vi-VN (TT200), "
             "chỉ giữa các mã cùng cấp. Khớp đúng một mã thì ghép (`mapping_basis = numeric_31122025`). "
             "`semantic_break`: mã ghép theo tên mà số lệch, không giống lỗi OCR (cùng tên, khác nội dung).", "",
             "| Kết quả | Số mã | Mã TT99 → mã TT200 khớp số |", "|---|---|---|"]
    for k, items in result.items():
        shown = ", ".join(f"{c} → {'/'.join(h) or '–'}" for c, _, h in items)
        lines.append(f"| {k} | {len(items)} | {shown} |")
    lines += ["", f"### Mã đã ghép biến động > {SHIFT_LIMIT:.0%} giữa 31/12/2025 (TT200) và đầu năm 2026 (TT99): "
              "nghi đổi nội dung, chờ duyệt", "",
              "Người dùng duyệt từng dòng: đổi nội dung thật thì đặt `semantic_break = true` trong "
              "`config/account_code_regime_map.csv` (tool tự gắn khi mọi báo cáo 2026 lệch và không giống lỗi OCR); "
              "biến động do phân loại lại một phần hoặc lỗi OCR thì ghi chú lại.", "",
              "| Mã TT99 | Mã TT200 | 31/12/2025 (TT200, VND) | Đầu năm 2026 (TT99, VND) | Biến động | semantic_break |",
              "|---|---|---|---|---|---|"]
    lines += [f"| {c} | {h[0]} | {h[1]:,} | {v:,} | {(v - h[1]) / h[1]:+.1%} | {'true' if h[2] else ''} |"
              for c, v, h in shifts] or ["| – | | | | | |"]
    lines += ["", MARK_END]
    text = NOTES.read_text(encoding="utf-8")
    block = "\n".join(lines)
    text = (text[:text.index(MARK_START)] + block + text[text.index(MARK_END) + len(MARK_END):]
            if MARK_START in text else text.rstrip() + "\n\n" + block + "\n")
    NOTES.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    from src.common.lock import run_locked
    sys.exit(run_locked(main, sys.argv[1:]))
