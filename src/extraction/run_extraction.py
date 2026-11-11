"""Bronze -> silver/fs_extracted_raw: bóc B01/B02/B03 từ mọi BCTC, đối chiếu ngày báo cáo, kiểm tra DQ.

    python -m src.extraction.run_extraction                 # mọi BCTC + kiểm tra kỳ báo cáo tháng
    python -m src.extraction.run_extraction --only c40e0d7f  # chỉ file có sha256 bắt đầu bằng ...
    python -m src.extraction.run_extraction --workers 4 --no-monthly

Kết quả: bảng Delta data/silver/fs_extracted_raw (ghi đè), mục tự sinh trong docs/dq_report.md,
và kỳ thật cho các file period=pending (mwg_crawler.resolve_period).
"""
from __future__ import annotations

import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path

import polars as pl

from src.common.paths import CONFIG_DIR, ROOT, SILVER_DIR
from src.extraction import ocr
from src.extraction.pdf_extractor import (
    apply_overrides, check_equations, check_parent_sums, detect_regime, expected_end, load_dim_account,
    load_equations, load_overrides, locate_statement_pages, parse_statement_page, report_end_date,
    signs_of, unit_multiplier,
)
from src.ingestion.mwg_crawler import PENDING, load_manifest, parse_period, resolve_period

RAW_TABLE = SILVER_DIR / "fs_extracted_raw"
DQ_REPORT = ROOT / "docs" / "dq_report.md"
OVERRIDES = CONFIG_DIR / "manual_overrides.csv"
EQUATIONS = CONFIG_DIR / "dq_equations.csv"
DIM_ACCOUNT = CONFIG_DIR / "dim_account.csv"
REGIME_MAP = CONFIG_DIR / "account_code_regime_map.csv"
BASE_REGIME = "TT200"  # mã chuẩn = mã TT200; regime khác quy đổi qua account_code_regime_map.csv
MARK_START, MARK_END = "<!-- extraction:start -->", "<!-- extraction:end -->"


def _candidates(row: dict) -> list[tuple[str, str]]:
    if row["period"] != PENDING:
        return [(row["doc_type"], row["period"])]
    return [tuple(c.split(":", 1)) for c in (row["period_candidates"] or "").split(";") if ":" in c]


def _check_period(row: dict, found: str | None, match) -> tuple[bool, tuple[str, str] | None]:
    """So kỳ đọc được trong file với kỳ trong manifest (hoặc các kỳ ứng viên nếu pending)."""
    hits = [c for c in _candidates(row) if match(c[1], found)]
    return (len(hits) == 1, hits[0] if len(hits) == 1 else None)


def extract_fs(row: dict) -> dict:
    """Chạy trong process con: tìm trang, OCR/đọc lớp chữ, dựng bảng. Không ghi gì ra ngoài cache OCR."""
    path, sha, text_layer = ROOT / row["path"], row["sha256"], bool(row["has_text_layer"])
    try:
        n = ocr.page_count(path)
        pages = locate_statement_pages(lambda p: ocr.page_text(path, sha, p, text_layer), n)
        cells, headers, units = [], [], {}
        for st, plist in pages.items():
            prev = None
            for p in plist:
                words = ocr.page_words(path, sha, p, text_layer)
                res = parse_statement_page(words, st, prev)
                if not text_layer and res["cols"]:
                    res = parse_statement_page(_with_column_ocr(path, sha, p, words, res), st, prev)
                prev = res if res["rows"] else prev
                headers.append(res["header_text"])
                units.setdefault(st, unit_multiplier(res["header_text"]))
                for r in res["rows"]:
                    for kind, v in zip(res["kinds"], r["values"]):
                        cells.append({"statement": st, "page": p, "account_code": r["account_code"],
                                      "item_name": r["item_name"], "period_kind": kind,
                                      "amount_raw": v, "raw_text": r["raw"]})
        for c in cells:  # không thấy dòng đơn vị tính: coi là VND (MWG in VND), ghi nhận ở unit_found
            raw = c.pop("amount_raw")
            c["amount_vnd"] = None if raw is None else raw * (units.get(c["statement"]) or 1)
        end = report_end_date("\n".join(headers))
        b01 = pages["B01"][0] if pages["B01"] else None
        regime = detect_regime(ocr.page_text(path, sha, b01, text_layer)) if b01 else None
        return {"pages": pages, "cells": cells, "end": end, "regime": regime,
                "unit_found": {k: v is not None for k, v in units.items()}, "error": None}
    except Exception as e:  # một file lỗi không được làm dừng cả lần chạy
        return {"pages": {}, "cells": [], "end": None, "regime": None, "unit_found": {},
                "error": f"{type(e).__name__}: {e}"}


def _with_column_ocr(path: Path, sha: str, page: int, words: list[dict], res: dict) -> list[dict]:
    """Thay số tiền và mã số OCR cả trang (vie+eng) bằng OCR riêng từng cột với whitelist ký tự.

    Cột tên chỉ tiêu giữ kết quả vie+eng. Mã in đậm (270, 440) hay bị đọc sai khi OCR cả trang.
    """
    h, top, bottom = res["h"], int(res["table_top"] - 0.3 * res["h"]), int(res["bottom"] + res["h"])
    amount_boxes = [(int(max(res["bounds"][i] + 0.3 * h, right - 1.05 * width - 0.3 * h)), top, int(right + 0.8 * h), bottom)
                    for i, (right, width) in enumerate(zip(res["cols"], res["col_widths"]))]
    code_box = (int(res["code_band"][0]), top, int(res["code_band"][1]), bottom)
    boxes = amount_boxes + [code_box]
    inside = lambda w: any(x0 <= (w["x0"] + w["x1"]) / 2 <= x1 and y0 <= (w["y0"] + w["y1"]) / 2 <= y1  # noqa: E731
                           for x0, y0, x1, y1 in boxes)
    new = [w for col in ocr.ocr_columns(path, sha, page, amount_boxes) for w in col]
    new += ocr.ocr_columns(path, sha, page, [code_box], config=ocr.CODE_CONFIG)[0]
    return [w for w in words if not inside(w)] + new


def check_monthly(row: dict) -> dict:
    path, sha = ROOT / row["path"], row["sha256"]
    try:
        text = ocr.page_text(path, sha, 1, bool(row["has_text_layer"]))
        top = " ".join(line for line in text.splitlines() if line.strip())[:400]
        return {"found": parse_period(top, "MONTHLY_RESULT"), "error": None}
    except Exception as e:
        return {"found": None, "error": f"{type(e).__name__}: {e}"}


def _dedupe(cells: list[dict]) -> list[dict]:
    """Một giá trị cho mỗi (báo cáo, mã, kỳ cột): giữ ô đầu tiên có số."""
    seen: dict[tuple, dict] = {}
    for c in cells:
        key = (c["statement"], c["account_code"], c["period_kind"])
        if key not in seen or (seen[key]["amount_vnd"] is None and c["amount_vnd"] is not None):
            seen[key] = c
    return list(seen.values())


def load_regime_map(path: Path) -> dict[tuple[str, str, str], str]:
    """{(regime, báo cáo, mã in trên báo cáo): mã chuẩn (= mã TT200)}."""
    if not Path(path).exists():
        return {}
    import csv
    with Path(path).open(encoding="utf-8", newline="") as f:
        return {(r["regime"], r["statement"], r["account_code"]): r["canonical_code"]
                for r in csv.DictReader(f) if r.get("canonical_code", "").strip()}


def canonical(regime: str | None, statement: str, code: str, regime_map: dict) -> str | None:
    if regime == BASE_REGIME:
        return code
    return regime_map.get((regime, statement, code))  # chưa có trong bảng map -> không so được


def _opening_check(doc: dict, closing_by_year: dict[int, dict]) -> tuple[bool | None, str]:
    """Số đầu năm N (B01) = số cuối năm N-1 của BCTC năm N-1, so theo mã chuẩn."""
    year = int(doc["period"][:4]) if doc["period"][:4].isdigit() else None
    prior = closing_by_year.get(year - 1) if year else None
    if not prior:
        return None, "không có BCTC năm trước"
    opening = {c["canonical_code"]: c["amount_vnd"] for c in doc["cells"]
               if c["statement"] == "B01" and c["period_kind"] == "opening" and c["amount_vnd"] is not None
               and c.get("canonical_code")}
    common = sorted(set(opening) & set(prior))
    if not common:
        if doc.get("regime") not in (None, BASE_REGIME):
            return None, f"chờ account_code_regime_map cho {doc.get('regime')}"
        return False, "không có mã chung để so"
    bad = [code for code in common if abs(opening[code] - prior[code]) > 1]
    return not bad, (f"lệch {len(bad)}/{len(common)} mã: {', '.join(bad[:5])}" if bad else f"{len(common)} mã khớp")


def run(only: list[str] | None = None, workers: int = 4, monthly: bool = True) -> dict:
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")  # mỗi process một luồng Tesseract
    mf = load_manifest()
    if only:
        mf = mf.filter(pl.any_horizontal([pl.col("sha256").str.starts_with(s) for s in only]))
    is_fs = pl.col("doc_type").str.starts_with("FS_") | pl.col("period_candidates").str.contains("FS_").fill_null(False)
    fs_rows = mf.filter(is_fs).sort("period").to_dicts()
    mo_rows = mf.filter(~is_fs & (pl.col("doc_type") == "MONTHLY_RESULT")).to_dicts() if monthly else []

    print(f"[extract] {len(fs_rows)} BCTC, {len(mo_rows)} báo cáo tháng, {workers} process")
    with ProcessPoolExecutor(max_workers=workers) as ex:
        fs_res = list(ex.map(extract_fs, fs_rows))
        mo_res = list(ex.map(check_monthly, mo_rows))

    overrides = load_overrides(OVERRIDES)
    equations, dim, regime_map = load_equations(EQUATIONS), load_dim_account(DIM_ACCOUNT), load_regime_map(REGIME_MAP)
    docs = []
    for row, res in zip(fs_rows, fs_res):
        regime = res["regime"]
        cells = _dedupe(res["cells"])
        for c in cells:
            c["source_doc_sha256"] = row["sha256"]
            c["canonical_code"] = canonical(regime, c["statement"], c["account_code"], regime_map)
        cells = apply_overrides(cells, overrides)
        found = res["end"].isoformat() if res["end"] else None
        ok, hit = _check_period(row, res["end"], lambda p, d: d is not None and expected_end(p) == d)
        values = {(c["statement"], c["account_code"], c["period_kind"]): c["amount_vnd"] for c in cells}
        checks = (check_equations(values, equations, regime, signs_of(dim, regime))
                  + check_parent_sums(values, dim, regime)) if cells and regime else []
        docs.append({**row, "res": res, "regime": regime, "cells": cells, "checks": checks, "report_end": found,
                     "period_check_ok": ok, "resolved": hit,
                     "dq_balance_ok": bool(checks) and all(c["ok"] for c in checks)})

    # Kỳ thật cho file pending
    for d in docs:
        if d["period"] == PENDING and d["resolved"]:
            resolve_period(d["url"], *d["resolved"])
            d["doc_type"], d["period"] = d["resolved"]

    # Số đầu năm N = số cuối năm N-1 (ưu tiên bản vi-VN như ở Silver)
    closing_by_year: dict[int, dict] = {}
    for d in sorted(docs, key=lambda d: d["lang"] != "vi-VN"):
        if d["doc_type"] == "FS_CONSOLIDATED_Y" and d["dq_balance_ok"] and d["period"].isdigit():
            closing_by_year.setdefault(int(d["period"]), {
                c["canonical_code"]: c["amount_vnd"] for c in d["cells"]
                if c["statement"] == "B01" and c["period_kind"] == "closing" and c["amount_vnd"] is not None
                and c["canonical_code"]})
    for d in docs:
        d["opening_ok"], d["opening_note"] = _opening_check(d, closing_by_year)
        d["passed"] = (d["dq_balance_ok"] and d["period_check_ok"] and d["opening_ok"] is not False
                       and bool(d["res"]["pages"].get("B01")) and bool(d["res"]["pages"].get("B02")))

    monthly_docs = []
    for row, res in zip(mo_rows, mo_res):
        ok, hit = _check_period(row, res["found"], lambda p, f: f == p)
        if row["period"] == PENDING and hit:
            resolve_period(row["url"], *hit)
        monthly_docs.append({**row, "found": res["found"], "period_check_ok": ok, "error": res["error"]})

    _write_raw(docs)
    summary = _write_report(docs, monthly_docs)
    print(f"[DQ] BCTC qua DQ: {summary['passed']}/{summary['total']} ({summary['rate']:.0%}); "
          f"báo cáo tháng đúng kỳ: {summary['mo_ok']}/{summary['mo_total']}")
    return summary


def _write_raw(docs: list[dict]) -> None:
    rows = [{"source_doc_sha256": d["sha256"], "url": d["url"], "lang": d["lang"], "doc_type": d["doc_type"],
             "period": d["period"], "regime": d["regime"], "statement": c["statement"], "page": c["page"],
             "account_code": c["account_code"], "canonical_code": c["canonical_code"], "item_name": c["item_name"], "period_kind": c["period_kind"],
             "amount_vnd": c["amount_vnd"], "raw_text": c["raw_text"], "is_manual": c["is_manual"],
             "dq_balance_ok": d["dq_balance_ok"], "period_check_ok": d["period_check_ok"],
             "extracted_at": datetime.now().isoformat(timespec="seconds")}
            for d in docs for c in d["cells"]]
    if not rows:
        return
    df = pl.DataFrame(rows, schema_overrides={"amount_vnd": pl.Int64, "page": pl.Int32})
    df.write_delta(str(RAW_TABLE), mode="overwrite", delta_write_options={"schema_mode": "overwrite"})
    print(f"[extract] Ghi {df.height} ô vào {RAW_TABLE.relative_to(ROOT)}")


def _mark(ok) -> str:
    return "✓" if ok is True else "✗" if ok is False else "–"


def _rate(group: list[dict]) -> str:
    n = len(group)
    return f"{sum(d['passed'] for d in group)}/{n} ({sum(d['passed'] for d in group) / n:.0%})" if n else "–"


def _breakdown(docs: list[dict], key, title: str) -> list[str]:
    groups: dict = {}
    for d in docs:
        groups.setdefault(key(d), []).append(d)
    return [f"| {title} | Qua DQ |", "|---|---|"] + [f"| {k} | {_rate(v)} |" for k, v in sorted(groups.items())] + [""]


def _write_report(docs: list[dict], monthly_docs: list[dict]) -> dict:
    total, passed = len(docs), sum(d["passed"] for d in docs)
    labels = list(dict.fromkeys(c["check"] for d in docs for c in d["checks"] if "Σ" not in c["check"]))
    dim_rows = len(load_dim_account(DIM_ACCOUNT))
    lines = [MARK_START, "", f"## 5. Bóc tách và DQ theo kỳ (tự sinh, {datetime.now():%Y-%m-%d %H:%M})", "",
             (f"**BCTC qua DQ: {_rate(docs)}** – chưa sửa tay gì "
              f"(`config/manual_overrides.csv` có {len(load_overrides(OVERRIDES))} dòng).") if total else "Chưa có BCTC.",
             "",
             "Điều kiện qua: tìm thấy B01 và B02; mọi phương trình trong `config/dq_equations.csv` của đúng regime "
             "đúng trên mọi cột; ngày báo cáo trong PDF khớp kỳ trong manifest; số đầu năm khớp số cuối năm trước "
             "(so theo mã chuẩn, khi có BCTC năm trước). Khoản giảm trừ chỉ lấy trị tuyệt đối khi `sign = -1` "
             f"trong `config/dim_account.csv` (hiện có {dim_rows} dòng)"
             + ("; kiểm tra tổng dòng con theo `parent_code` **chưa chạy** vì danh mục còn trống." if not dim_rows
                else "."),
             ""]
    lines += _breakdown(docs, lambda d: d["regime"] or "không nhận diện", "Regime")
    lines += _breakdown(docs, lambda d: d["lang"], "Ngôn ngữ")
    lines += _breakdown(docs, lambda d: d["period"][:4], "Năm")
    lines += ["| Kỳ | Lang | Regime | Trang B01 / B02 / B03 | Số ô | "
              + " | ".join(labels) + " | Đầu năm = cuối năm trước | Ngày trong PDF | Khớp kỳ | Kết quả | Ghi chú |",
              "|" + "---|" * (len(labels) + 10)]
    for d in sorted(docs, key=lambda d: (d["period"], d["lang"])):
        pages = d["res"]["pages"]
        notes = [f"{c['check']}: {c['note']}" for c in d["checks"] if not c["ok"] and c["note"]][:3]
        if d["res"]["error"]:
            notes.insert(0, d["res"]["error"][:80])
        if d["opening_ok"] is not True:
            notes.append(d["opening_note"])
        cols = []
        for label in labels:
            cs = [c for c in d["checks"] if c["check"] == label]
            cols.append((_mark(all(c["ok"] for c in cs)) + f" ({sum(c['ok'] for c in cs)}/{len(cs)})") if cs else "–")
        lines.append(
            f"| {d['period']} | {d['lang'][:2]} | {d['regime'] or '?'} "
            f"| {'/'.join(','.join(map(str, pages.get(s, []))) or '–' for s in ('B01', 'B02', 'B03'))} "
            f"| {sum(c['amount_vnd'] is not None for c in d['cells'])} | {' | '.join(cols)} | {_mark(d['opening_ok'])} "
            f"| {d['report_end'] or '–'} | {_mark(d['period_check_ok'])} | {'**qua**' if d['passed'] else 'không qua'} "
            f"| {'; '.join(notes).replace('|', '/')} |")
    mo_ok = sum(m["period_check_ok"] for m in monthly_docs)
    bad = [m for m in monthly_docs if not m["period_check_ok"]]
    lines += ["", f"**Báo cáo tháng – kỳ đọc trong file khớp manifest: {mo_ok}/{len(monthly_docs)}.** "
              "Bảng số liệu theo chuỗi (`chain_kpi`) làm ở tuần 6.", ""]
    if bad:
        lines += ["| Kỳ (manifest) | Lang | Kỳ đọc được | Ghi chú |", "|---|---|---|---|"]
        lines += [f"| {m['period']} | {m['lang'][:2]} | {m['found'] or '–'} | {(m['error'] or '')[:80]} |"
                  for m in sorted(bad, key=lambda m: m["period"])]
    lines += ["", MARK_END]
    block = "\n".join(lines)
    text = DQ_REPORT.read_text(encoding="utf-8")
    if MARK_START in text:
        text = text[:text.index(MARK_START)] + block + text[text.index(MARK_END) + len(MARK_END):]
    else:
        text = text.rstrip() + "\n\n" + block + "\n"
    DQ_REPORT.write_text(text, encoding="utf-8")
    return {"total": total, "passed": passed, "rate": passed / total if total else 0.0,
            "mo_total": len(monthly_docs), "mo_ok": mo_ok}


def main(argv: list[str]) -> int:
    only = [a for a in argv if re.fullmatch(r"[0-9a-f]{6,64}", a)]
    workers = int(argv[argv.index("--workers") + 1]) if "--workers" in argv else 4
    run(only or None, workers=workers, monthly="--no-monthly" not in argv)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
