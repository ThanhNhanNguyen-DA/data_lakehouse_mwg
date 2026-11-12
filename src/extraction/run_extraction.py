"""Bronze -> silver/fs_extracted_raw: bóc B01/B02/B03 từ mọi BCTC, đối chiếu ngày báo cáo, kiểm tra DQ.

    python -m src.extraction.run_extraction                 # mọi BCTC + kiểm tra kỳ báo cáo tháng
    python -m src.extraction.run_extraction --only c40e0d7f  # chỉ file có sha256 bắt đầu bằng ...
    python -m src.extraction.run_extraction --workers 4 --no-monthly --no-paddle

Thứ tự tự sửa lỗi OCR: OCR lại dải mã bị thiếu và ô số sai mẫu (Tesseract 400 dpi) -> bỏ phiếu giữa
các lần in lại một số B01 -> PaddleOCR cho báo cáo còn sai -> gợi ý sửa ô theo phương trình
(ghi vào manual_overrides.csv với status=pending, KHÔNG áp dụng tới khi người dùng xác nhận).

Kết quả: bảng Delta data/silver/fs_extracted_raw (ghi đè), mục tự sinh trong docs/dq_report.md,
và kỳ thật cho các file period=pending (mwg_crawler.resolve_period).
"""
from __future__ import annotations

import calendar
import csv
import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime
from pathlib import Path

import polars as pl

from src.common.paths import CONFIG_DIR, ROOT, SILVER_DIR
from src.extraction import ocr
from src.extraction.pdf_extractor import (
    _is_grouped_amount, apply_overrides, check_equations, check_parent_sums, check_q1_columns, detect_regime,
    eligible_equations, expected_end, load_dim_account, load_equations, load_overrides, locate_statement_pages,
    parse_statement_page, report_end_date, signs_of, unit_multiplier,
)
from src.extraction.self_correct import apply_consensus, suggest_fixes, write_suggestions
from src.ingestion.mwg_crawler import PENDING, load_manifest, parse_period, resolve_period

RAW_TABLE = SILVER_DIR / "fs_extracted_raw"
DQ_REPORT = ROOT / "docs" / "dq_report.md"
OVERRIDES = CONFIG_DIR / "manual_overrides.csv"
EQUATIONS = CONFIG_DIR / "dq_equations.csv"
DIM_ACCOUNT = CONFIG_DIR / "dim_account.csv"
REGIME_MAP = CONFIG_DIR / "account_code_regime_map.csv"
BASE_REGIME = "TT200"  # mã chuẩn = mã TT200; regime khác quy đổi qua account_code_regime_map.csv
FIRST_TT99_YEAR = 2026  # regime không nhận diện được: năm trước đó coi là TT200 (regime_inferred)
MAX_PUBLISH_LAG_DAYS = 125  # ngày kỳ đọc từ PDF phải trước ngày đăng và không quá ~4 tháng
MARK_START, MARK_END = "<!-- extraction:start -->", "<!-- extraction:end -->"


# ---------- Bóc một file (chạy trong process con) ----------

def extract_fs(row: dict, engine: str = "tesseract", statements: tuple[str, ...] = ("B01", "B02", "B03")) -> dict:
    """Tìm trang, OCR/đọc lớp chữ, dựng bảng. Không ghi gì ra ngoài cache OCR."""
    path, sha, text_layer = ROOT / row["path"], row["sha256"], bool(row["has_text_layer"])
    stats = {"codes_recovered": 0, "cells_reocr": 0}
    try:
        pages = locate_statement_pages(lambda p: ocr.page_text(path, sha, p, text_layer), ocr.page_count(path))
        cells, headers, units, b02_header_ok = [], [], {}, None
        for st in statements:
            prev = None
            for p in pages[st]:
                if engine == "paddle":
                    res = parse_statement_page(ocr.paddle_words(path, sha, p), st, prev)
                else:
                    words = ocr.page_words(path, sha, p, text_layer)
                    res = parse_statement_page(words, st, prev)
                    if not text_layer and res["cols"]:
                        words = _with_column_ocr(path, sha, p, words, res)
                        res = parse_statement_page(words, st, prev)
                        words, n_codes, n_cells = _reocr_fixes(path, sha, p, words, res)
                        if n_codes or n_cells:
                            res = parse_statement_page(words, st, prev)
                            stats["codes_recovered"] += n_codes
                            stats["cells_reocr"] += n_cells
                if st == "B02" and b02_header_ok is None and res["cols"]:
                    b02_header_ok = res["header_ok"]
                prev = res if res["rows"] else prev
                headers.append(res["header_text"])
                units.setdefault(st, unit_multiplier(res["header_text"]))
                for r in res["rows"]:
                    for kind, v in zip(res["kinds"], r["values"]):
                        cells.append({"statement": st, "page": p, "account_code": r["account_code"],
                                      "item_name": r["item_name"], "period_kind": kind,
                                      "amount_raw": v, "raw_text": r["raw"]})
        for c in cells:  # không thấy dòng đơn vị tính: coi là VND (MWG in VND)
            raw = c.pop("amount_raw")
            c["amount_vnd"] = None if raw is None else raw * (units.get(c["statement"]) or 1)
        b01 = pages["B01"][0] if pages["B01"] else None
        regime = detect_regime(ocr.page_text(path, sha, b01, text_layer)) if b01 else None
        return {"pages": pages, "cells": cells, "end": report_end_date("\n".join(headers)), "regime": regime,
                "b02_header_ok": b02_header_ok, "stats": stats, "error": None}
    except Exception as e:  # một file lỗi không được làm dừng cả lần chạy
        return {"pages": {}, "cells": [], "end": None, "regime": None, "b02_header_ok": None, "stats": stats,
                "error": f"{type(e).__name__}: {e}"}


def _inside(w: dict, box) -> bool:
    x0, y0, x1, y1 = box
    return x0 <= (w["x0"] + w["x1"]) / 2 <= x1 and y0 <= (w["y0"] + w["y1"]) / 2 <= y1


def _with_column_ocr(path: Path, sha: str, page: int, words: list[dict], res: dict) -> list[dict]:
    """Thay số tiền và mã số OCR cả trang (vie+eng) bằng OCR riêng từng cột với whitelist ký tự.

    Cột tên chỉ tiêu giữ kết quả vie+eng. Mã in đậm (270, 440) hay bị đọc sai khi OCR cả trang.
    """
    h, top, bottom = res["h"], int(res["table_top"] - 0.3 * res["h"]), int(res["bottom"] + res["h"])
    amount_boxes = [(int(max(res["bounds"][i] + 0.3 * h, right - 1.05 * width - 0.3 * h)), top, int(right + 0.8 * h), bottom)
                    for i, (right, width) in enumerate(zip(res["cols"], res["col_widths"]))]
    code_box = (int(res["code_band"][0]), top, int(res["code_band"][1]), bottom)
    boxes = amount_boxes + [code_box]
    new = [w for col in ocr.ocr_columns(path, sha, page, amount_boxes) for w in col]
    new += ocr.ocr_columns(path, sha, page, [code_box], config=ocr.CODE_CONFIG)[0]
    return [w for w in words if not any(_inside(w, b) for b in boxes)] + new


def _reocr_fixes(path: Path, sha: str, page: int, words: list[dict], res: dict) -> tuple[list[dict], int, int]:
    """OCR lại (400 dpi, làm mảnh nét, --psm 7): dải mã của dòng có số mà thiếu mã; ô số sai mẫu nhóm 3 chữ số."""
    h, (cx0, cx1) = res["h"], res["code_band"]
    out, n_codes, n_cells = list(words), 0, 0
    for y0, y1 in res["orphans"]:
        code = ocr.ocr_region(path, sha, page, (cx0, y0 - 0.3 * h, cx1, y1 + 0.3 * h), ocr.REOCR_CODE)
        if re.fullmatch(r"\d{1,3}[ab]?", code):
            mid = (cx0 + cx1) / 2
            out.append({"text": code, "x0": mid - h, "y0": y0, "x1": mid + h, "y1": y1})
            n_codes += 1
    for box in res["suspects"]:
        x0, y0, x1, y1 = box
        text = ocr.ocr_region(path, sha, page, (x0 - 0.3 * h, y0 - 0.3 * h, x1 + 0.3 * h, y1 + 0.3 * h),
                              ocr.REOCR_NUMBER)
        if _is_grouped_amount(text):
            out = [w for w in out if not _inside(w, box)] + [{"text": text, "x0": x0, "y0": y0, "x1": x1, "y1": y1}]
            n_cells += 1
    return out, n_codes, n_cells


def check_monthly(row: dict) -> dict:
    path, sha = ROOT / row["path"], row["sha256"]
    try:
        text = ocr.page_text(path, sha, 1, bool(row["has_text_layer"]))
        top = " ".join(line for line in text.splitlines() if line.strip())[:400]
        return {"found": parse_period(top, "MONTHLY_RESULT"), "error": None}
    except Exception as e:
        return {"found": None, "error": f"{type(e).__name__}: {e}"}


# ---------- Kỳ báo cáo ----------

def _candidates(row: dict) -> list[tuple[str, str]]:
    if row["period"] != PENDING:
        return [(row["doc_type"], row["period"])]
    return [tuple(c.split(":", 1)) for c in (row["period_candidates"] or "").split(";") if ":" in c]


def _check_period(row: dict, found: str | None, match) -> tuple[bool, tuple[str, str] | None]:
    """So kỳ đọc được trong file với kỳ trong manifest (hoặc các kỳ ứng viên nếu pending)."""
    hits = [c for c in _candidates(row) if match(c[1], found)]
    return (len(hits) == 1, hits[0] if len(hits) == 1 else None)


def plausible_end(end: date, published: str | None) -> bool:
    """Kỳ đọc từ PDF phải kết thúc trước ngày đăng và không quá ~4 tháng (OCR hay đọc 3 thành 5)."""
    if not published:
        return True
    pub = date.fromisoformat(published[:10])
    return end < pub and (pub - end).days <= MAX_PUBLISH_LAG_DAYS


def _month_end(period: str | None) -> date | None:
    m = re.fullmatch(r"(\d{4})-M(\d{2})", period or "")
    if not m:
        return None
    y, mo = int(m.group(1)), int(m.group(2))
    return date(y, mo, calendar.monthrange(y, mo)[1])


def _period_result(row: dict, end: date | None, match) -> dict:
    """period_check_ok / ocr_date_suspect / kỳ thật cho file pending. Ngày khó tin -> giữ kỳ theo tiêu đề."""
    if end is not None and not plausible_end(end, row.get("published")):
        return {"period_check_ok": row["period"] != PENDING, "ocr_date_suspect": True, "resolved": None}
    ok, hit = _check_period(row, end, match)
    return {"period_check_ok": ok, "ocr_date_suspect": False, "resolved": hit}


# ---------- Mã chuẩn, đầu năm = cuối năm trước ----------

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


# ---------- Ghép kết quả và kiểm tra ----------

def _make_doc(row: dict, res: dict, ctx: dict) -> dict:
    regime, inferred = res["regime"], False
    if regime is None and row["period"][:4].isdigit() and int(row["period"][:4]) < FIRST_TT99_YEAR:
        regime, inferred = BASE_REGIME, True
    cells = _dedupe(res["cells"])
    for c in cells:
        c["source_doc_sha256"] = row["sha256"]
        c["canonical_code"] = canonical(regime, c["statement"], c["account_code"], ctx["regime_map"])
    period = _period_result(row, res["end"], lambda p, d: d is not None and expected_end(p) == d)
    return {**row, "res": res, "regime": regime, "regime_inferred": inferred,
            "cells": apply_overrides(cells, ctx["overrides"]), "report_end": res["end"].isoformat() if res["end"] else None,
            **period, "engine": "tesseract"}


def _evaluate(d: dict, ctx: dict) -> None:
    """Kiểm tra DQ của một báo cáo (ghi vào d): phương trình, tổng dòng con, Q1, cột B02 TT99."""
    values = {(c["statement"], c["account_code"], c["period_kind"]): c["amount_vnd"] for c in d["cells"]}
    regime = d["regime"]
    signs = signs_of(ctx["dim"], regime) if regime else {}
    checks = (check_equations(values, ctx["equations"], regime, signs) + check_parent_sums(values, ctx["dim"], regime)
              if d["cells"] and regime else [])
    if d["period"].endswith("-Q1"):
        checks += check_q1_columns(values)
    eligible = {e["label"] for e in eligible_equations(ctx["equations"], signs)}
    d["columns_ok"] = not (regime == "TT99" and d["res"]["b02_header_ok"] is False)
    d["checks"] = checks
    d["eligible_failed"] = [c for c in checks if not c["ok"] and c["check"] in eligible]
    d["b01_ok"] = any(c["check"] in eligible and c.get("equation", {}).get("statement") == "B01" for c in checks) and \
        all(c["ok"] for c in checks if c.get("equation", {}).get("statement") == "B01")
    d["dq_balance_ok"] = bool(checks) and all(c["ok"] for c in checks) and d["columns_ok"]
    d["values"], d["signs"] = values, signs


def _paddle_fallback(docs: list[dict], ctx: dict) -> tuple[int, int]:
    """PaddleOCR riêng các báo cáo (B01/B02) còn sai phương trình đánh giá được; giữ kết quả nếu ít lỗi hơn."""
    pages_run = replaced = 0
    for d in docs:
        stmts = sorted({c["equation"]["statement"] for c in d["eligible_failed"] if "equation" in c})
        for st in stmts:
            pages_run += len(d["res"]["pages"].get(st, []))
            res = extract_fs(d, engine="paddle", statements=(st,))
            if res["error"]:
                continue
            new = [c for c in _dedupe(res["cells"])]
            for c in new:
                c["source_doc_sha256"] = d["sha256"]
                c["canonical_code"] = canonical(d["regime"], c["statement"], c["account_code"], ctx["regime_map"])
            trial = {**d, "cells": [c for c in d["cells"] if c["statement"] != st] + apply_overrides(new, ctx["overrides"])}
            _evaluate(trial, ctx)
            before = sum(1 for c in d["eligible_failed"] if c["equation"]["statement"] == st)
            after = sum(1 for c in trial["eligible_failed"] if c.get("equation", {}).get("statement") == st)
            if after < before:
                d["cells"], d["engine"] = trial["cells"], f"paddle:{st}"
                _evaluate(d, ctx)
                replaced += 1
    return pages_run, replaced


def run(only: list[str] | None = None, workers: int = 4, monthly: bool = True, paddle: bool = True) -> dict:
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

    ctx = {"overrides": load_overrides(OVERRIDES), "equations": load_equations(EQUATIONS),
           "dim": load_dim_account(DIM_ACCOUNT), "regime_map": load_regime_map(REGIME_MAP)}
    docs = [_make_doc(row, res, ctx) for row, res in zip(fs_rows, fs_res)]
    for d in docs:  # kỳ thật cho file pending
        if d["period"] == PENDING and d["resolved"]:
            resolve_period(d["url"], *d["resolved"])
            d["doc_type"], d["period"] = d["resolved"]

    stats = {"consensus": apply_consensus(docs), "paddle_pages": 0, "paddle_replaced": 0, "suggested": 0}
    for d in docs:
        _evaluate(d, ctx)
    if paddle:
        stats["paddle_pages"], stats["paddle_replaced"] = _paddle_fallback(docs, ctx)
        if stats["paddle_replaced"]:
            stats["consensus"] += apply_consensus(docs)
            for d in docs:
                _evaluate(d, ctx)
    for d in docs:
        stats["suggested"] += write_suggestions(OVERRIDES, d, suggest_fixes(d, d["eligible_failed"], d["values"], d["signs"]))

    # Số đầu năm N = số cuối năm N-1 (ưu tiên bản vi-VN như ở Silver)
    closing_by_year: dict[int, dict] = {}
    for d in sorted(docs, key=lambda d: d["lang"] != "vi-VN"):
        if d["doc_type"] == "FS_CONSOLIDATED_Y" and d["b01_ok"] and d["period"].isdigit():
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
        p = _period_result(row, _month_end(res["found"]), lambda per, end: end is not None and _month_end(per) == end)
        if row["period"] == PENDING and p["resolved"]:
            resolve_period(row["url"], *p["resolved"])
        monthly_docs.append({**row, **p, "found": res["found"], "error": res["error"]})

    stats["pending"] = sum(1 for r in _read_overrides() if r.get("status") == "pending")
    _write_raw(docs)
    summary = _write_report(docs, monthly_docs, stats)
    print(f"[DQ] B01 cân đối: {summary['b01']}; BCTC qua DQ: {summary['passed']}/{summary['total']}; "
          f"báo cáo tháng đúng kỳ: {summary['mo_ok']}/{summary['mo_total']}; ô chờ xác nhận: {stats['pending']}")
    return summary


def _read_overrides() -> list[dict]:
    if not OVERRIDES.exists():
        return []
    with OVERRIDES.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


# ---------- Ghi kết quả ----------

def _write_raw(docs: list[dict]) -> None:
    rows = [{"source_doc_sha256": d["sha256"], "url": d["url"], "lang": d["lang"], "doc_type": d["doc_type"],
             "period": d["period"], "regime": d["regime"], "regime_inferred": d["regime_inferred"],
             "statement": c["statement"], "page": c["page"], "account_code": c["account_code"],
             "canonical_code": c["canonical_code"], "item_name": c["item_name"], "period_kind": c["period_kind"],
             "amount_vnd": c["amount_vnd"], "raw_text": c["raw_text"], "is_manual": c["is_manual"],
             "is_consensus": c.get("is_consensus", False), "corrected_by": c.get("corrected_by"),
             "ocr_engine": d["engine"], "dq_balance_ok": d["dq_balance_ok"], "columns_ok": d["columns_ok"],
             "period_check_ok": d["period_check_ok"], "ocr_date_suspect": d["ocr_date_suspect"],
             "extracted_at": datetime.now().isoformat(timespec="seconds")}
            for d in docs for c in d["cells"]]
    if not rows:
        return
    df = pl.DataFrame(rows, schema_overrides={"amount_vnd": pl.Int64, "page": pl.Int32, "corrected_by": pl.Utf8})
    df.write_delta(str(RAW_TABLE), mode="overwrite", delta_write_options={"schema_mode": "overwrite"})
    print(f"[extract] Ghi {df.height} ô vào {RAW_TABLE.relative_to(ROOT)}")


def _mark(ok) -> str:
    return "✓" if ok is True else "✗" if ok is False else "–"


def _rate(group: list[dict], key: str = "passed") -> str:
    n, k = len(group), sum(bool(d[key]) for d in group)
    return f"{k}/{n} ({k / n:.0%})" if n else "–"


def _breakdown(docs: list[dict], key, title: str) -> list[str]:
    groups: dict = {}
    for d in docs:
        groups.setdefault(key(d), []).append(d)
    return ([f"| {title} | B01 cân đối | Qua DQ toàn bộ |", "|---|---|---|"]
            + [f"| {k} | {_rate(v, 'b01_ok')} | {_rate(v)} |" for k, v in sorted(groups.items())] + [""])


def _write_report(docs: list[dict], monthly_docs: list[dict], stats: dict) -> dict:
    total, passed = len(docs), sum(d["passed"] for d in docs)
    labels = list(dict.fromkeys(c["check"] for d in docs for c in d["checks"] if "Σ" not in c["check"]))
    dim_rows = len(load_dim_account(DIM_ACCOUNT))
    lines = [MARK_START, "", f"## 5. Bóc tách và DQ theo kỳ (tự sinh, {datetime.now():%Y-%m-%d %H:%M})", "",
             f"**B01 cân đối: {_rate(docs, 'b01_ok')}. BCTC qua DQ toàn bộ: {_rate(docs)}.** "
             f"Chưa áp dụng gợi ý sửa tay nào; **{stats['pending']} ô đang chờ xác nhận** trong "
             "`config/manual_overrides.csv` (`status=pending`).", "",
             f"Tự sửa lỗi OCR lần này: {stats['consensus']} ô sửa/điền theo bỏ phiếu giữa các lần in lại số B01; "
             f"PaddleOCR chạy {stats['paddle_pages']} trang, thay kết quả cho {stats['paddle_replaced']} báo cáo; "
             f"{sum(d['res']['stats']['codes_recovered'] for d in docs)} mã và "
             f"{sum(d['res']['stats']['cells_reocr'] for d in docs)} ô số được OCR lại riêng (400 dpi).", "",
             "Điều kiện qua: tìm thấy B01 và B02; mọi phương trình trong `config/dq_equations.csv` của đúng regime "
             "đúng trên mọi cột; báo cáo Q1 có cột quý này = lũy kế; cột B02 TT99 đọc được tiêu đề; ngày báo cáo "
             "trong PDF khớp kỳ; số đầu năm khớp số cuối năm trước (so theo mã chuẩn). Khoản giảm trừ chỉ lấy trị "
             f"tuyệt đối khi `sign = -1` trong `config/dim_account.csv` (hiện có {dim_rows} dòng)"
             + ("; **chưa có sign nên mọi phương trình B02 có vế trừ của TT200 đều trượt**, và kiểm tra tổng "
                "dòng con theo `parent_code` chưa chạy." if not dim_rows else "."),
             ""]
    lines += _breakdown(docs, lambda d: d["regime"] + (" (suy ra)" if d["regime_inferred"] else "") if d["regime"]
                        else "không nhận diện", "Regime")
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
        if not d["columns_ok"]:
            notes.append("không đọc được tiêu đề cột B02 (TT99): không đưa sang Silver")
        if d["ocr_date_suspect"]:
            notes.append("ngày OCR khó tin (ocr_date_suspect), giữ kỳ theo tiêu đề")
        if d["engine"] != "tesseract":
            notes.append(f"dùng {d['engine']}")
        cols = []
        for label in labels:
            cs = [c for c in d["checks"] if c["check"] == label]
            cols.append((_mark(all(c["ok"] for c in cs)) + f" ({sum(c['ok'] for c in cs)}/{len(cs)})") if cs else "–")
        regime = (d["regime"] or "?") + ("*" if d["regime_inferred"] else "")
        lines.append(
            f"| {d['period']} | {d['lang'][:2]} | {regime} "
            f"| {'/'.join(','.join(map(str, pages.get(s, []))) or '–' for s in ('B01', 'B02', 'B03'))} "
            f"| {sum(c['amount_vnd'] is not None for c in d['cells'])} | {' | '.join(cols)} | {_mark(d['opening_ok'])} "
            f"| {d['report_end'] or '–'} | {_mark(d['period_check_ok'])} | {'**qua**' if d['passed'] else 'không qua'} "
            f"| {'; '.join(notes).replace('|', '/')} |")
    lines += ["", "\\* regime suy ra theo năm (`regime_inferred`): không đọc được tên B01, năm < 2026 nên coi là TT200."]
    mo_ok = sum(m["period_check_ok"] for m in monthly_docs)
    flagged = [m for m in monthly_docs if not m["period_check_ok"] or m["ocr_date_suspect"]]
    lines += ["", f"**Báo cáo tháng – kỳ đọc trong file khớp manifest: {mo_ok}/{len(monthly_docs)}.** "
              "Bảng số liệu theo chuỗi (`chain_kpi`) làm ở tuần 6.", ""]
    if flagged:
        lines += ["| Kỳ (manifest) | Lang | Kỳ đọc được | Ghi chú |", "|---|---|---|---|"]
        lines += [f"| {m['period']} | {m['lang'][:2]} | {m['found'] or '–'} | "
                  f"{'ocr_date_suspect: giữ kỳ theo tiêu đề' if m['ocr_date_suspect'] else (m['error'] or '')[:80]} |"
                  for m in sorted(flagged, key=lambda m: m["period"])]
    lines += ["", MARK_END]
    block = "\n".join(lines)
    text = DQ_REPORT.read_text(encoding="utf-8")
    if MARK_START in text:
        text = text[:text.index(MARK_START)] + block + text[text.index(MARK_END) + len(MARK_END):]
    else:
        text = text.rstrip() + "\n\n" + block + "\n"
    DQ_REPORT.write_text(text, encoding="utf-8")
    return {"total": total, "passed": passed, "b01": _rate(docs, "b01_ok"),
            "mo_total": len(monthly_docs), "mo_ok": mo_ok}


def main(argv: list[str]) -> int:
    only = [a for a in argv if re.fullmatch(r"[0-9a-f]{6,64}", a)]
    workers = int(argv[argv.index("--workers") + 1]) if "--workers" in argv else 4
    run(only or None, workers=workers, monthly="--no-monthly" not in argv, paddle="--no-paddle" not in argv)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
