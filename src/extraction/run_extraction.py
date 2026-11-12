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
    parse_statement_page, report_end_date, signs_of, to_number, unit_multiplier,
)
from src.extraction.self_correct import apply_consensus, suggest_fixes, write_suggestions
from src.quality.monthly_check import TOL_BILLION, monthly_crosscheck, one_digit_off
from src.quality.monthly_check import report_lines as monthly_report_lines
from src.ingestion.mwg_crawler import PENDING, load_manifest, parse_period, resolve_period

RAW_TABLE = SILVER_DIR / "fs_extracted_raw"
DQ_REPORT = ROOT / "docs" / "dq_report.md"
OVERRIDES = CONFIG_DIR / "manual_overrides.csv"
EQUATIONS = CONFIG_DIR / "dq_equations.csv"
DIM_ACCOUNT = CONFIG_DIR / "dim_account.csv"
REGIME_MAP = CONFIG_DIR / "account_code_regime_map.csv"
IDENTITIES = CONFIG_DIR / "dq_identities.csv"
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
        b01 = pages["B01"][0] if pages["B01"] else None
        regime = detect_regime(ocr.page_text(path, sha, b01, text_layer)) if b01 else None
        cells, raw_cells, headers, units, b02_method = [], [], [], {}, None
        for st in statements:
            prev = None
            for p in pages[st]:
                raw_res = None
                if engine == "paddle":
                    words = [{**w, "origin": "paddle"} for w in ocr.paddle_words(path, sha, p)]
                    res = parse_statement_page(words, st, prev)
                else:
                    words = ocr.page_words(path, sha, p, text_layer)
                    res = parse_statement_page(words, st, prev)
                    if not text_layer and res["cols"]:
                        words = _with_column_ocr(path, sha, p, words, res)
                        res = raw_res = parse_statement_page(words, st, prev)
                        words, n_codes, n_cells = _reocr_fixes(path, sha, p, words, res)
                        if n_codes or n_cells:
                            res = parse_statement_page(words, st, prev)
                            stats["codes_recovered"] += n_codes
                            stats["cells_reocr"] += n_cells
                        if st == "B02" and regime == "TT99" and b02_method is None and not res["header_ok"]:
                            res2 = parse_statement_page(_with_header_ocr(path, sha, p, words, res), st, prev)
                            if res2["header_ok"] and len(res2["kinds"]) == len(res["kinds"]):
                                res, b02_method = res2, "header_reocr"
                if st == "B02" and b02_method is None and res["cols"]:
                    b02_method = "header" if res["header_ok"] else "positional"
                prev = res if res["rows"] else prev
                headers.append(res["header_text"])
                units.setdefault(st, unit_multiplier(res["header_text"]))
                cells += _cells_of(res, st, p)
                raw_cells += _cells_of(raw_res or res, st, p)
        for c in cells + raw_cells:  # không thấy dòng đơn vị tính: coi là VND (MWG in VND)
            raw = c.pop("amount_raw")
            c["amount_vnd"] = None if raw is None else raw * (units.get(c["statement"]) or 1)
        return {"pages": pages, "cells": cells, "raw_cells": raw_cells, "end": report_end_date("\n".join(headers)),
                "regime": regime, "b02_method": b02_method, "stats": stats, "error": None}
    except Exception as e:  # một file lỗi không được làm dừng cả lần chạy
        return {"pages": {}, "cells": [], "raw_cells": [], "end": None, "regime": None, "b02_method": None,
                "stats": stats, "error": f"{type(e).__name__}: {e}"}


def _cells_of(res: dict, st: str, page: int) -> list[dict]:
    return [{"statement": st, "page": page, "account_code": r["account_code"], "item_name": r["item_name"],
             "period_kind": kind, "amount_raw": v, "raw_text": r["raw"], "value_origin": origin or "ocr",
             "row_y0": r["y0"], "row_y1": r["y1"], "col_x1": x1, "col_w": w, "line_h": res["h"]}
            for r in res["rows"]
            for kind, v, origin, x1, w in zip(res["kinds"], r["values"], r["origins"], res["cols"], res["col_widths"])]


def _with_header_ocr(path: Path, sha: str, page: int, words: list[dict], res: dict) -> list[dict]:
    """OCR lại dải tiêu đề cột (chữ nghiêng nhỏ của B02 TT99) ở 500 dpi, thay cho chữ OCR cả trang."""
    h, top = res["h"], res["table_top"]
    box = (res["bounds"][0] - h, top - 7 * h, res["cols"][-1] + h, top - 0.2 * h)
    return [w for w in words if not _inside(w, box)] + ocr.ocr_band_words(path, sha, page, box)


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
            out.append({"text": code, "x0": mid - h, "y0": y0, "x1": mid + h, "y1": y1, "origin": "ocr_reocr"})
            n_codes += 1
    for box in res["suspects"]:
        x0, y0, x1, y1 = box
        i = min(range(len(res["cols"])), key=lambda j: abs(x1 - res["cols"][j]))  # OCR lại cả bề ngang ô của cột
        cx0, cx1 = res["cols"][i] - 1.05 * res["col_widths"][i] - 0.3 * h, res["cols"][i] + 0.8 * h
        text = ocr.ocr_region(path, sha, page, (cx0, y0 - 0.3 * h, cx1, y1 + 0.3 * h), ocr.REOCR_NUMBER)
        if _is_grouped_amount(text):
            out = [w for w in out if not _inside(w, box)] + [{"text": text, "x0": cx0, "y0": y0,
                                                              "x1": res["cols"][i], "y1": y1,
                                                              "origin": "ocr_reocr"}]
            n_cells += 1
    return out, n_codes, n_cells


REREAD_DPIS = (400, 500, 600)


def reread_cell(row: dict, c: dict, accept) -> int | None:
    """OCR lại riêng một ô số tiền trên PDF BCTC gốc (400/500/600 dpi); trả số đầu tiên được accept(số) chấp nhận.

    Chỉ cho bản scan (tọa độ ô theo pass 2, 300 dpi). Dùng để lấy giá trị gợi ý từ chính PDF, không bịa số."""
    if row.get("has_text_layer") or c.get("col_x1") is None or c.get("row_y0") is None:
        return None
    h = c["line_h"]
    box = (c["col_x1"] - 1.05 * c["col_w"] - 0.3 * h, c["row_y0"] - 0.3 * h, c["col_x1"] + 0.8 * h, c["row_y1"] + 0.3 * h)
    for dpi in REREAD_DPIS:
        text = ocr.ocr_region(ROOT / row["path"], row["sha256"], c["page"], box, ocr.REOCR_NUMBER, dpi=dpi)
        if not _is_grouped_amount(text):
            continue
        try:
            v = to_number(text)
        except ValueError:
            continue
        if v is not None and accept(int(v)):
            return int(v)
    return None


def monthly_suggestions(xcheck: list[dict]) -> list[tuple[dict, dict]]:
    """Ô B02 lệch báo cáo tháng >= 1 tỷ và khác đúng một chữ số (tỷ đồng): đọc lại ô trên PDF BCTC; số đọc lại
    khớp báo cáo tháng (±1 tỷ) thì gợi ý pending (suggested_by = monthly_crosscheck)."""
    out = []
    for r in xcheck:
        near = r.get("nearest")
        if r["found"] or near is None or abs(near - r["billion"]) < 1 or not one_digit_off(near, r["billion"]):
            continue
        d, c = r["doc"], r["cell"]
        v = reread_cell(d, c, lambda x: abs(round(abs(x) / 1e9) - near) <= TOL_BILLION and x != abs(c["amount_vnd"]))
        r["reread"] = v
        if v is None:  # ghi số đọc lại được (nếu có) để người dùng thấy BCTC vẫn đọc ra số cũ
            r["reread_any"] = reread_cell(d, c, lambda x: True)
        if v is not None:
            out.append((d, {"statement": "B02", "account_code": c["account_code"], "period_kind": c["period_kind"],
                            "amount_vnd": v * (-1 if c["amount_vnd"] < 0 else 1), "ocr_value": c["amount_vnd"],
                            "reason": f"Báo cáo tháng {r['month']} in {near:,} tỷ, BCTC OCR {r['billion']:,} tỷ "
                                      f"(lệch một chữ số); số đọc lại từ PDF trang {c['page']}"}))
    return out


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


def load_semantic_breaks(path: Path) -> set[tuple[str, str, str]]:
    """Mã TT99 ghép theo tên nhưng khác nội dung với mã TT200 (semantic_break = true trong bảng map)."""
    if not Path(path).exists():
        return set()
    with Path(path).open(encoding="utf-8", newline="") as f:
        return {(r["regime"], r["statement"], r["account_code"]) for r in csv.DictReader(f)
                if (r.get("semantic_break") or "").strip().lower() == "true"}


def load_identities(path: Path) -> list[dict]:
    """Đồng nhất thức trong config/dq_identities.csv: ô target lấy giá trị ô source_code (cùng báo cáo, cùng cột)."""
    if not Path(path).exists():
        return []
    with Path(path).open(encoding="utf-8", newline="") as f:
        return [r for r in csv.DictReader(f) if r.get("target", "").strip()]


def apply_identities(cells: list[dict], identities: list[dict], regime: str | None) -> list[dict]:
    """Gán target := source_code trên mọi cột có source (value_origin = identity, giữ số OCR ở ocr_value).
    Ô nhập tay (is_manual) giữ nguyên."""
    out = list(cells)
    for idt in (i for i in identities if i["regime"] == regime):
        st, target, src = idt["statement"], idt["target"], idt["source_code"]
        sources = {c["period_kind"]: c for c in out if c["statement"] == st and c["account_code"] == src
                   and c["amount_vnd"] is not None}
        for kind, s in sources.items():
            cur = next((c for c in out if c["statement"] == st and c["account_code"] == target
                        and c["period_kind"] == kind), None)
            if cur is not None and cur.get("is_manual"):
                continue
            new = {**(cur or {**s, "item_name": "", "raw_text": "", "row_y0": None, "row_y1": None}),
                   "account_code": target, "amount_vnd": s["amount_vnd"], "value_origin": "identity",
                   "ocr_value": cur["amount_vnd"] if cur else None, "identity_of": src}
            out = [c for c in out if c is not cur] + [new]
    return out


def canonical(regime: str | None, statement: str, code: str, regime_map: dict) -> str | None:
    if regime == BASE_REGIME:
        return code
    return regime_map.get((regime, statement, code))  # chưa có trong bảng map -> không so được


def _opening_check(doc: dict, closing_by_year: dict[int, dict],
                   restated: dict | None = None) -> tuple[bool | None, str]:
    """Số đầu năm N (B01) = số cuối năm N-1 của BCTC năm N-1, so theo mã chuẩn (bỏ mã trình bày lại)."""
    year = int(doc["period"][:4]) if doc["period"][:4].isdigit() else None
    prior = closing_by_year.get(year - 1) if year else None
    if prior and restated:
        prior = {k: v for k, v in prior.items() if (year - 1, k) not in restated}
    if not prior:
        return None, "không có BCTC năm trước"
    opening = {c["canonical_code"]: c["amount_vnd"] for c in doc["cells"]
               if c["statement"] == "B01" and c["period_kind"] == "opening" and c["amount_vnd"] is not None
               and c.get("canonical_code") and not c.get("semantic_break")}
    common = sorted(set(opening) & set(prior))
    if not common:
        if doc.get("regime") not in (None, BASE_REGIME):
            return None, f"chờ account_code_regime_map cho {doc.get('regime')}"
        return False, "không có mã chung để so"
    bad = [code for code in common if abs(opening[code] - prior[code]) > 1]
    return not bad, (f"lệch {len(bad)}/{len(common)} mã: {', '.join(bad[:5])}" if bad else f"{len(common)} mã khớp")


# ---------- Ghép kết quả và kiểm tra ----------

MIN_LABEL_MATCH = 3  # số mã chuẩn tối thiểu phải khớp để chứng minh nhãn cột bằng số


def _b02_columns(d: dict) -> dict[str, dict[str, int]]:
    """{nhãn cột hiện tại: {mã chuẩn: số}} của B02."""
    out: dict[str, dict[str, int]] = {}
    for c in d["cells"]:
        if c["statement"] == "B02" and c["amount_vnd"] is not None and c.get("canonical_code"):
            out.setdefault(c["period_kind"], {})[c["canonical_code"]] = c["amount_vnd"]
    return out


def _matches(a: dict | None, b: dict | None) -> int:
    return sum(abs(a[k] - b[k]) <= 1 for k in set(a or {}) & set(b or {}))


def _diff(a: dict, b: dict) -> dict:
    return {k: a[k] - b[k] for k in set(a) & set(b)}


def relabel_b02_numeric(d: dict, docs: list[dict]) -> bool:
    """Gắn nhãn cột B02 bằng số khi không đọc được tiêu đề cột (không theo thứ tự cột).

    Hai cột năm trước phải khớp báo cáo cùng kỳ năm trước (>= 3 mã chuẩn); hai cột năm nay phân biệt bằng
    lũy kế - quý này = lũy kế kỳ trước cùng năm (Q2: Q1, Q3: bán niên); Q1: hai cột năm nay bằng nhau.
    """
    by_period = {}
    for x in sorted(docs, key=lambda x: (x["lang"] != "vi-VN", not x["columns_ok"])):
        by_period.setdefault(x["period"], x)
    cols, part = _b02_columns(d), d["period"][5:]
    year = int(d["period"][:4])
    prior = by_period.get(f"{year - 1}-{part}" if part else str(year - 1))
    pc = _b02_columns(prior) if prior else {}
    mapping, evidence = {}, []
    if len(cols) == 2:
        score = {k: _matches(v, pc.get("current")) for k, v in cols.items()}
        best = max(score, key=score.get)
        if score[best] >= MIN_LABEL_MATCH and all(score[k] < MIN_LABEL_MATCH for k in score if k != best):
            mapping = {best: "prior", next(k for k in cols if k != best): "current"}
            evidence.append(f"cột năm trước khớp {prior['period']} ({score[best]} mã)")
    elif len(cols) == 4 and pc.get("q_current") and pc.get("ytd_current"):
        kq = max(cols, key=lambda k: _matches(cols[k], pc["q_current"]))
        ky = max((k for k in cols if k != kq), key=lambda k: _matches(cols[k], pc["ytd_current"]))
        mq, my = _matches(cols[kq], pc["q_current"]), _matches(cols[ky], pc["ytd_current"])
        a, b = [k for k in cols if k not in (kq, ky)]
        if mq >= MIN_LABEL_MATCH and my >= MIN_LABEL_MATCH:
            mapping = {kq: "q_prior", ky: "ytd_prior"}
            evidence.append(f"cột năm trước khớp {prior['period']} ({mq}/{my} mã)")
            if part == "Q1":
                if _matches(cols[a], cols[b]) >= MIN_LABEL_MATCH:
                    mapping.update({a: "q_current", b: "ytd_current"})  # Q1: quý này = lũy kế, hai cột bằng nhau
                    evidence.append("Q1: hai cột năm nay bằng nhau")
            else:
                prev = by_period.get(f"{year}-Q1") if part == "Q2" else by_period.get(f"{year}-H1")
                pv = _b02_columns(prev).get("ytd_current" if part == "Q2" else "current") if prev else None
                for ytd, q in ((a, b), (b, a)):
                    m = _matches(_diff(cols[ytd], cols[q]), pv)
                    if m >= MIN_LABEL_MATCH:
                        mapping.update({ytd: "ytd_current", q: "q_current"})
                        evidence.append(f"lũy kế - quý này = {prev['period']} ({m} mã)")
                        break
    if len(mapping) != len(cols) or len(cols) < 2:
        d["label_note"] = "không chứng minh được nhãn cột bằng số" + (": " + "; ".join(evidence) if evidence else "")
        return False
    for c in d["cells"]:
        if c["statement"] == "B02":
            c["period_kind"] = mapping.get(c["period_kind"], c["period_kind"])
    d.update(columns_ok=True, b02_label_method="numeric", label_note="; ".join(evidence))
    return True


def _prepare_cells(cells: list[dict], row: dict, regime: str | None, ctx: dict) -> list[dict]:
    """Ô vừa bóc -> sửa tay (overrides), đồng nhất thức, mã chuẩn, cờ semantic_break."""
    for c in cells:
        c["source_doc_sha256"] = row["sha256"]
    cells = [{**c, "value_origin": "manual"} if c["is_manual"] else c for c in apply_overrides(cells, ctx["overrides"])]
    cells = apply_identities(cells, ctx.get("identities", []), regime)
    year = int(row["period"][:4]) if row["period"][:4].isdigit() else None
    for c in cells:
        c["canonical_code"] = canonical(regime, c["statement"], c["account_code"], ctx["regime_map"])
        # cột đầu năm của báo cáo năm đầu TT99 là ngày 31/12 còn theo TT200: mã đổi nội dung không so chéo được
        c["semantic_break"] = (c["statement"] == "B01" and c["period_kind"] == "opening" and year == FIRST_TT99_YEAR
                               and (regime, c["statement"], c["account_code"]) in ctx.get("semantic_breaks", set()))
    return cells


def _make_doc(row: dict, res: dict, ctx: dict) -> dict:
    regime, inferred = res["regime"], False
    if regime is None and row["period"][:4].isdigit() and int(row["period"][:4]) < FIRST_TT99_YEAR:
        regime, inferred = BASE_REGIME, True
    cells = _prepare_cells(_dedupe(res["cells"]), row, regime, ctx)
    raw_values = {(c["statement"], c["account_code"], c["period_kind"]): c["amount_vnd"]
                  for c in _dedupe(res.get("raw_cells", []))}
    period = _period_result(row, res["end"], lambda p, d: d is not None and expected_end(p) == d)
    method = res.get("b02_method")
    return {**row, "res": res, "regime": regime, "regime_inferred": inferred, "cells": cells, "raw_values": raw_values,
            "report_end": res["end"].isoformat() if res["end"] else None, **period, "engine": "tesseract",
            "b02_label_method": method, "label_note": "",
            # TT99 in thứ tự cột B02 khác TT200: chỉ tin nhãn đọc từ tiêu đề cột (hoặc chứng minh bằng số sau)
            "columns_ok": not (regime == "TT99" and method not in ("header", "header_reocr"))}


def _evaluate(d: dict, ctx: dict) -> None:
    """Kiểm tra DQ của một báo cáo (ghi vào d): phương trình, tổng dòng con, Q1, cột B02 TT99."""
    values = {(c["statement"], c["account_code"], c["period_kind"]): c["amount_vnd"] for c in d["cells"]}
    regime = d["regime"]
    signs = signs_of(ctx["dim"], regime) if regime else {}
    skip = {(i["statement"], i["target"]) for i in ctx.get("identities", []) if i["regime"] == regime}
    checks = (check_equations(values, ctx["equations"], regime, signs)
              + check_parent_sums(values, ctx["dim"], regime, skip=skip) if d["cells"] and regime else [])
    if d["period"].endswith("-Q1"):
        checks += check_q1_columns(values)
    eligible = {e["label"] for e in eligible_equations(ctx["equations"], signs)}
    d["checks"] = checks
    # phương trình (dq_equations) sai -> PaddleOCR; cả phương trình lẫn tổng dòng con sai -> gợi ý sửa một ô
    d["eq_failed"] = [c for c in checks if not c["ok"] and c["check"] in eligible]
    d["eligible_failed"] = [c for c in checks if not c["ok"] and (c["check"] in eligible or "Σ" in c["check"])]
    b01 = [c for c in checks if c["check"] in eligible and c.get("equation", {}).get("statement") == "B01"]
    d["b01_ok"] = bool(b01) and all(c["ok"] for c in b01)
    d["dq_balance_ok"] = bool(checks) and all(c["ok"] for c in checks) and d["columns_ok"]
    d["values"], d["signs"] = values, signs
    for c in checks:
        c["level"] = dq_level(c)
    d["critical_failed"] = [c for c in checks if c["level"] == "critical" and not c["ok"]]
    covered = {c["equation"]["lhs"] for c in checks if c["level"] == "critical"
               and c.get("equation", {}).get("statement") == "B02"}
    d["critical_unchecked"] = sorted(CRITICAL_B02 - covered)  # vd. TT200 chưa có công thức chắc chắn cho 30, 60
    # Ô trusted: nằm trong một phương trình/tổng dòng con đúng, hoặc >= 2 nguồn bỏ phiếu khớp
    d["trusted_keys"] = {(c["equation"]["statement"], code, c["kind"]) for c in checks
                         if c["ok"] and c.get("equation") and c["kind"]
                         for code in [c["equation"]["lhs"]] + [t for _, t in c["equation"]["terms"]]}
    d["trusted_keys"] |= {(c["statement"], c["account_code"], c["period_kind"]) for c in d["cells"] if c.get("is_consensus")}
    # ô gán bằng đồng nhất thức (440 := 270) tin được khi ô nguồn tin được
    d["trusted_keys"] |= {(c["statement"], c["account_code"], c["period_kind"]) for c in d["cells"]
                          if c.get("identity_of") and (c["statement"], c["identity_of"], c["period_kind"]) in d["trusted_keys"]}


CRITICAL_B02 = {"10", "20", "30", "50", "60"}  # mã B02 có phương trình chặn Gold (người dùng chỉ định)


def dq_level(check: dict) -> str:
    """critical (chặn Gold): phương trình cân đối B01, phương trình B02 có vế trái 10/20/30/50/60.
    warning (chỉ ghi lại): tổng dòng con các mục nhỏ, kiểm tra Q1 quý này = lũy kế."""
    eq = check.get("equation") or {}
    if eq.get("statement") == "B01" and "Σ" not in check["check"]:
        return "critical"
    if eq.get("statement") == "B02" and eq.get("lhs") in CRITICAL_B02:
        return "critical"
    return "warning"


def critical_columns(d: dict) -> dict[str, bool]:
    """{period_kind: qua critical B02 của riêng cột đó}. Điều kiện chung của báo cáo (trang B02, kỳ/ngày, nhãn cột
    TT99) vẫn áp cho mọi cột; cột không có phương trình critical nào kiểm được thì không qua."""
    if not (d["res"]["pages"].get("B02") and d["period_check_ok"] and d["columns_ok"]):
        return {}
    out: dict[str, bool] = {}
    for c in d["checks"]:
        if c["level"] == "critical" and c.get("equation", {}).get("statement") == "B02" and c.get("kind"):
            out[c["kind"]] = out.get(c["kind"], True) and c["ok"]
    return out


def detect_restatements(docs: list[dict], closing_by_year: dict[int, dict]) -> dict[tuple[int, str], dict]:
    """Số đầu năm của MỌI báo cáo năm N+1 (>= 2 báo cáo) khớp nhau nhưng khác số cuối năm N: trình bày lại.

    Không coi là lỗi OCR: bỏ khỏi kiểm tra đầu năm = cuối năm trước, ghi vào mục "Kỳ bị trình bày lại".
    """
    openings: dict[tuple[int, str], list[tuple[dict, int]]] = {}
    for d in docs:
        if not d["period"][:4].isdigit():
            continue
        for c in d["cells"]:
            if c["statement"] == "B01" and c["period_kind"] == "opening" and c["amount_vnd"] is not None \
                    and c.get("canonical_code") and not c.get("semantic_break"):
                openings.setdefault((int(d["period"][:4]) - 1, c["canonical_code"]), []).append((d, c["amount_vnd"]))
    out = {}
    for (year, code), vals in openings.items():
        closing = closing_by_year.get(year, {}).get(code)
        first = vals[0][1]
        if closing is None or len(vals) < 2 or any(abs(v - first) > 1 for _, v in vals) or abs(first - closing) <= 1:
            continue
        out[(year, code)] = {"closing": closing, "restated": first, "docs": sorted({d["period"] for d, _ in vals}),
                             "note": _restatement_note(docs, year, vals, closing, first)}
    return out


def _one_digit_more(a: int, b: int) -> bool:
    """Chuỗi số a bằng b chèn thêm đúng một chữ số (dấu hiệu OCR đọc thừa chữ số, không phải trình bày lại)."""
    sa, sb = str(abs(a)), str(abs(b))
    return len(sa) == len(sb) + 1 and any(sa[:i] + sa[i + 1:] == sb for i in range(len(sa)))


def _restatement_note(docs: list[dict], year: int, vals: list, closing: int, restated: int) -> str:
    notes = []
    before = {d.get("regime", "TT200") for d in docs if d["period"].startswith(str(year))}
    after = {d.get("regime", "TT200") for d, _ in vals}
    if before and after != before:
        notes.append(f"đổi chế độ kế toán ({'/'.join(sorted(before))} → {'/'.join(sorted(after))}): "
                     "cùng tên mã nhưng có thể khác nội dung, cần xem lại bảng map")
    if _one_digit_more(closing, restated):
        notes.append(f"nghi OCR đọc thừa một chữ số ở BCTC năm {year}")
    elif _one_digit_more(restated, closing):
        notes.append(f"nghi OCR đọc thiếu một chữ số ở BCTC năm {year}")
    return "; ".join(notes)


def digit_suggestions(restated: dict, closing_doc: dict[int, dict]) -> list[tuple[int, dict]]:
    """Số cuối năm N lệch đúng một chữ số thừa/thiếu so với số đầu năm (khớp nhau) của mọi báo cáo N+1:
    gợi ý sửa ô cuối năm N theo số kỳ liền kề (pending, người dùng xác nhận trên PDF)."""
    out = []
    for (year, code), v in sorted(restated.items()):
        if year not in closing_doc or not (_one_digit_more(v["closing"], v["restated"])
                                           or _one_digit_more(v["restated"], v["closing"])):
            continue
        cell = next((c for c in closing_doc[year]["cells"] if c["statement"] == "B01" and c["period_kind"] == "closing"
                     and c.get("canonical_code") == code), None)
        if cell:
            out.append((year, {"statement": "B01", "account_code": cell["account_code"], "period_kind": "closing",
                               "amount_vnd": v["restated"], "ocr_value": v["closing"],
                               "reason": f"Lệch đúng một chữ số so với số đầu năm (khớp nhau) của mọi báo cáo "
                                         f"{year + 1}: {', '.join(v['docs'])}"}))
    return out


def _paddle_fallback(docs: list[dict], ctx: dict) -> tuple[int, int]:
    """PaddleOCR riêng các báo cáo (B01/B02) còn sai phương trình đánh giá được; giữ kết quả nếu ít lỗi hơn."""
    pages_run = replaced = 0
    for d in docs:
        stmts = sorted({c["equation"]["statement"] for c in d["eq_failed"] if "equation" in c})
        for st in stmts:
            if st == "B02" and d["b02_label_method"] == "numeric":
                continue  # PaddleOCR sẽ gắn lại nhãn cột theo thứ tự, mất nhãn đã chứng minh bằng số
            pages_run += len(d["res"]["pages"].get(st, []))
            res = extract_fs(d, engine="paddle", statements=(st,))
            if res["error"]:
                continue
            new = _prepare_cells(_dedupe(res["cells"]), d, d["regime"], ctx)
            trial = {**d, "cells": [c for c in d["cells"] if c["statement"] != st] + new}
            _evaluate(trial, ctx)
            before = sum(1 for c in d["eq_failed"] if c["equation"]["statement"] == st)
            after = sum(1 for c in trial["eq_failed"] if c.get("equation", {}).get("statement") == st)
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
           "dim": load_dim_account(DIM_ACCOUNT), "regime_map": load_regime_map(REGIME_MAP),
           "identities": load_identities(IDENTITIES), "semantic_breaks": load_semantic_breaks(REGIME_MAP)}
    docs = [_make_doc(row, res, ctx) for row, res in zip(fs_rows, fs_res)]
    for d in docs:  # kỳ thật cho file pending
        if d["period"] == PENDING and d["resolved"]:
            resolve_period(d["url"], *d["resolved"])
            d["doc_type"], d["period"] = d["resolved"]

    for d in sorted(docs, key=lambda d: d["period"]):  # H1 trước Q1-Q3: Q3 cần nhãn của H1 cùng năm
        if not d["columns_ok"]:
            relabel_b02_numeric(d, docs)
    # Độ chính xác OCR thô: phương trình B01 đánh giá được trên giá trị trước mọi bước tự sửa
    for d in docs:
        signs = signs_of(ctx["dim"], d["regime"]) if d["regime"] else {}
        b01_eqs = [e for e in eligible_equations(ctx["equations"], signs) if e["statement"] == "B01"]
        raw = check_equations(d["raw_values"], b01_eqs, d["regime"], signs) if d["regime"] else []
        d["b01_raw_ok"] = bool(raw) and all(c["ok"] for c in raw)
        d["raw_checks"] = raw
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
    closing_doc: dict[int, dict] = {}
    for d in sorted(docs, key=lambda d: d["lang"] != "vi-VN"):
        if d["doc_type"] == "FS_CONSOLIDATED_Y" and d["b01_ok"] and d["period"].isdigit():
            closing_doc.setdefault(int(d["period"]), d)
            closing_by_year.setdefault(int(d["period"]), {
                c["canonical_code"]: c["amount_vnd"] for c in d["cells"]
                if c["statement"] == "B01" and c["period_kind"] == "closing" and c["amount_vnd"] is not None
                and c["canonical_code"]})
    restated = detect_restatements(docs, closing_by_year)
    stats["digit_suggested"] = sum(write_suggestions(OVERRIDES, closing_doc[year], [s], suggested_by="adjacent_period")
                                   for year, s in digit_suggestions(restated, closing_doc))
    for d in docs:
        d["opening_ok"], d["opening_note"] = _opening_check(d, closing_by_year, restated)
        has_pages = bool(d["res"]["pages"].get("B01")) and bool(d["res"]["pages"].get("B02"))
        d["passed"] = d["dq_balance_ok"] and d["period_check_ok"] and d["opening_ok"] is not False and has_pages
        # critical chặn Gold; warning (tổng dòng con mục nhỏ, đầu năm ≠ cuối năm trước) chỉ ghi lại
        # critical theo từng báo cáo: B01 chỉ chặn ô B01, B02 chỉ chặn ô B02 (B03 chưa có critical -> chưa vào Gold)
        for st in ("B01", "B02"):
            crit = [c for c in d["checks"] if c["level"] == "critical" and c.get("equation", {}).get("statement") == st]
            d[f"passed_critical_{st.lower()}"] = (bool(d["res"]["pages"].get(st)) and d["period_check_ok"]
                                                  and (st == "B01" or d["columns_ok"])
                                                  and bool(crit) and all(c["ok"] for c in crit))
        d["passed_critical"] = has_pages and d["passed_critical_b01"] and d["passed_critical_b02"]
        b02_cols = critical_columns(d)
        for c in d["cells"]:
            c["is_trusted"] = (c["statement"], c["account_code"], c["period_kind"]) in d["trusted_keys"]
            # B02 chặn theo từng cột (period_kind): cột lũy kế trượt không kéo cột quý này ra khỏi Gold
            ok = b02_cols.get(c["period_kind"], False) if c["statement"] == "B02" else d.get("passed_critical_b01", False) \
                if c["statement"] == "B01" else False
            c["gold_eligible"] = c["is_trusted"] and ok

    monthly_docs = []
    for row, res in zip(mo_rows, mo_res):
        p = _period_result(row, _month_end(res["found"]), lambda per, end: end is not None and _month_end(per) == end)
        if row["period"] == PENDING and p["resolved"]:
            resolve_period(row["url"], *p["resolved"])
        monthly_docs.append({**row, **p, "found": res["found"], "error": res["error"]})

    stats["monthly_xcheck"] = monthly_crosscheck(docs, mo_rows)
    stats["monthly_suggested"] = sum(write_suggestions(OVERRIDES, d, [s], suggested_by="monthly_crosscheck")
                                     for d, s in monthly_suggestions(stats["monthly_xcheck"]))
    stats["pending"] = sum(1 for r in _read_overrides() if r.get("status") == "pending")
    stats["restated"] = restated
    _write_raw(docs)
    summary = _write_report(docs, monthly_docs, stats)
    print(f"[DQ] qua critical: {_rate(docs, 'passed_critical')}; ô trusted: {_trusted(docs)}")
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
             "value_origin": c.get("value_origin", "ocr"), "b02_label_method": d["b02_label_method"],
             "is_trusted": c.get("is_trusted", False), "passed_critical": d["passed_critical"],
             "passed_critical_b01": d["passed_critical_b01"], "passed_critical_b02": d["passed_critical_b02"],
             "gold_eligible": c.get("gold_eligible", False), "semantic_break": c.get("semantic_break", False),
             "ocr_value": c.get("ocr_value"),
             "row_y0": c.get("row_y0"), "row_y1": c.get("row_y1"),
             "ocr_engine": d["engine"], "dq_balance_ok": d["dq_balance_ok"], "columns_ok": d["columns_ok"],
             "period_check_ok": d["period_check_ok"], "ocr_date_suspect": d["ocr_date_suspect"],
             "extracted_at": datetime.now().isoformat(timespec="seconds")}
            for d in docs for c in d["cells"]]
    if not rows:
        return
    df = pl.DataFrame(rows, schema_overrides={"amount_vnd": pl.Int64, "ocr_value": pl.Int64, "page": pl.Int32,
                                              "corrected_by": pl.Utf8,
                                              "row_y0": pl.Float64, "row_y1": pl.Float64})
    df.write_delta(str(RAW_TABLE), mode="overwrite", delta_write_options={"schema_mode": "overwrite"})
    print(f"[extract] Ghi {df.height} ô vào {RAW_TABLE.relative_to(ROOT)}")


def _mark(ok) -> str:
    return "✓" if ok is True else "✗" if ok is False else "–"


def _rate(group: list[dict], key: str = "passed") -> str:
    n, k = len(group), sum(bool(d[key]) for d in group)
    return f"{k}/{n} ({k / n:.0%})" if n else "–"


def _trusted(docs: list[dict], gold: bool = False, statement: str | None = None) -> str:
    cells = [c for d in docs for c in d["cells"] if c["amount_vnd"] is not None
             and (statement is None or c["statement"] == statement)]
    k = sum(c.get("gold_eligible" if gold else "is_trusted", False) for c in cells)
    return f"{k}/{len(cells)} ({k / len(cells):.0%})" if cells else "–"


def _col_rate(docs: list[dict]) -> str:
    cols = [ok for d in docs for ok in critical_columns(d).values()]
    return f"{sum(cols)}/{len(cols)}" if cols else "–"


def _unchecked(docs: list[dict]) -> dict[str, set[str]]:
    """Mã B02 critical không kiểm được ở ít nhất một báo cáo của regime (gộp theo regime, không in lặp)."""
    out: dict[str, set[str]] = {}
    for d in docs:
        if d["regime"] and d["critical_unchecked"]:
            out.setdefault(d["regime"], set()).update(d["critical_unchecked"])
    return out


def _eq_rate(docs: list[dict], key: str) -> str:
    checks = [c for d in docs for c in d.get(key, [])]
    ok = sum(c["ok"] for c in checks)
    return f"{ok}/{len(checks)} ({ok / len(checks):.0%})" if checks else "–"


def _origins(docs: list[dict]) -> list[tuple[str, int]]:
    from collections import Counter
    return sorted(Counter(c.get("value_origin", "ocr") for d in docs for c in d["cells"]
                          if c["amount_vnd"] is not None).items())


def _methods(docs: list[dict]) -> list[tuple[str, int]]:
    from collections import Counter
    return sorted(Counter(d["b02_label_method"] or "không có B02" for d in docs).items())


def _breakdown(docs: list[dict], key, title: str) -> list[str]:
    groups: dict = {}
    for d in docs:
        groups.setdefault(key(d), []).append(d)
    return ([f"| {title} | B01 cân đối | **Critical B01** | **Critical B02** | Critical cả hai | Qua DQ toàn bộ |",
             "|---|---|---|---|---|---|"]
            + [f"| {k} | {_rate(v, 'b01_ok')} | {_rate(v, 'passed_critical_b01')} | {_rate(v, 'passed_critical_b02')} "
               f"| {_rate(v, 'passed_critical')} | {_rate(v)} |"
               for k, v in sorted(groups.items())] + [""])


def _top_failures(docs: list[dict], n: int = 10) -> list[str]:
    from collections import Counter
    fails = Counter((d["regime"], c["check"], c["level"]) for d in docs for c in {(c["check"], c["level"]): c
                    for c in d["checks"] if not c["ok"]}.values())
    totals = Counter((d["regime"], c["check"]) for d in docs for c in {c["check"]: c for c in d["checks"]}.values())
    rows = ["| # | Regime | Phương trình | Mức | Số báo cáo trượt / được kiểm |", "|---|---|---|---|---|"]
    for i, ((reg, label, level), k) in enumerate(fails.most_common(n), 1):
        rows.append(f"| {i} | {reg} | {label} | {level} | {k}/{totals[(reg, label)]} |")
    return rows + [""]


def _parent_stats(docs: list[dict]) -> list[str]:
    """Tỷ lệ trượt từng phương trình cha–con; > 50% báo cáo: nghi lỗi dim_account/mẫu MWG (kèm ví dụ số)."""
    stats: dict[tuple[str, str], list] = {}
    for d in docs:
        for label in {c["check"] for c in d["checks"] if "Σ" in c["check"]}:
            cs = [c for c in d["checks"] if c["check"] == label]
            s_ = stats.setdefault((d["regime"], label), [0, 0, None])
            s_[1] += 1
            bad = next((c for c in cs if not c["ok"]), None)
            if bad:
                s_[0] += 1
                if s_[2] is None and "lệch" in bad["note"]:
                    eq, kind = bad["equation"], bad["kind"]
                    kids = ", ".join(f"{t}={d['values'].get((eq['statement'], t, kind)) or 0:,}" for _, t in eq["terms"])
                    s_[2] = (f"{d['period']} {d['lang'][:2]} {kind}: {eq['lhs']}="
                             f"{d['values'].get((eq['statement'], eq['lhs'], kind)) or 0:,}; con: {kids}")
    rows = ["| Regime | Phương trình | Trượt / kiểm | Tỷ lệ |", "|---|---|---|---|"]
    suspect = []
    for (reg, label), (k, n, ex) in sorted(stats.items(), key=lambda x: -x[1][0] / x[1][1]):
        rows.append(f"| {reg} | {label} | {k}/{n} | {k / n:.0%} |")
        if k / n > 0.5:
            suspect.append(f"- **{reg} {label}**: trượt {k}/{n} báo cáo. Ví dụ: {ex or 'chỉ có lỗi thiếu mã'}")
    head = (["**Nghi lỗi dim_account/mẫu MWG** (trượt > 50% báo cáo):", ""] + suspect + [""]
            if suspect else ["Không có phương trình cha–con nào trượt > 50% báo cáo.", ""])
    return head + rows + [""]


def _restated_table(restated: dict) -> list[str]:
    if not restated:
        return ["Không có mã nào bị trình bày lại.", ""]
    rows = ["| Năm (31/12) | Mã chuẩn | Cuối năm N (BCTC năm N) | Đầu năm N+1 (mọi báo cáo N+1) | Chênh lệch | Báo cáo N+1 | Ghi chú |",
            "|---|---|---|---|---|---|---|"]
    for (year, code), v in sorted(restated.items()):
        diff = v["restated"] - v["closing"]
        pct = f"{diff / v['closing']:.2%}" if v["closing"] else "–"
        rows.append(f"| {year} | {code} | {v['closing']:,} | {v['restated']:,} | {diff:,} ({pct}) | {', '.join(v['docs'])} | {v.get('note', '')} |")
    return rows + [""]


def _write_report(docs: list[dict], monthly_docs: list[dict], stats: dict) -> dict:
    total, passed = len(docs), sum(d["passed"] for d in docs)
    labels = list(dict.fromkeys(c["check"] for d in docs for c in d["checks"] if "Σ" not in c["check"]))
    dim_rows = len(load_dim_account(DIM_ACCOUNT))
    lines = [MARK_START, "", f"## 5. Bóc tách và DQ theo kỳ (tự sinh, {datetime.now():%Y-%m-%d %H:%M})", "",
             f"**B01 cân đối: {_rate(docs, 'b01_ok')}. BCTC qua DQ toàn bộ: {_rate(docs)}.** "
             f"Chưa áp dụng gợi ý sửa tay nào; **{stats['pending']} ô đang chờ xác nhận** trong "
             "`config/manual_overrides.csv` (`status=pending`).", "",
             f"**Độ chính xác OCR thô** (trước mọi bước tự sửa): B01 cân đối {_rate(docs, 'b01_raw_ok')}; "
             f"{_eq_rate(docs, 'raw_checks')} phép kiểm phương trình B01 đúng. **Sau tự sửa**: B01 cân đối "
             f"{_rate(docs, 'b01_ok')}.", "",
             f"**Qua critical (chặn Gold theo từng báo cáo): B01 {_rate(docs, 'passed_critical_b01')}, "
             f"B02 {_rate(docs, 'passed_critical_b02')}** (cả hai: {_rate(docs, 'passed_critical')}). Ô trusted (nằm "
             f"trong một phương trình/tổng dòng con đúng, >= 2 nguồn bỏ phiếu khớp, hoặc đồng nhất thức với ô "
             f"trusted): {_trusted(docs)}. **Ô vào được Gold** (trusted và critical của chính báo cáo đó qua): "
             f"B01 {_trusted(docs, True, 'B01')}, B02 {_trusted(docs, True, 'B02')}; B03 chưa có critical nên chưa "
             "vào Gold. B02 chặn theo từng cột (`period_kind`): cột B02 qua critical "
             f"{_col_rate(docs)}.", "",
             "Critical B01: phương trình cân đối B01, kỳ/ngày báo cáo, có trang B01. Critical B02: phương trình B02 có "
             "vế trái 10, 20, 30, 50, 60, kỳ/ngày báo cáo, có trang B02, nhãn cột B02 TT99. Đồng nhất thức "
             "(`config/dq_identities.csv`, `value_origin = identity`) thay phép kiểm tổng dòng con của dòng đích. Warning (chỉ ghi lại): tổng dòng con các mục nhỏ, Q1 quý này = lũy "
             "kế, đầu năm ≠ cuối năm trước. Critical chưa kiểm được (thiếu công thức chắc chắn): "
             + "; ".join(f"{reg}: B02 {', '.join(sorted(codes))}" for reg, codes in sorted(_unchecked(docs).items()))
             + ".", "",
             "Nguồn gốc giá trị (`value_origin`): " + ", ".join(f"{k} {v}" for k, v in _origins(docs)) + ". "
             "Nhãn cột B02 (`b02_label_method`): " + ", ".join(f"{k} {v}" for k, v in _methods(docs)) + ".", "",
             f"Tự sửa lỗi OCR lần này: {stats['consensus']} ô sửa/điền theo bỏ phiếu giữa các lần in lại số B01; "
             f"PaddleOCR chạy {stats['paddle_pages']} trang, thay kết quả cho {stats['paddle_replaced']} báo cáo; "
             f"{sum(d['res']['stats']['codes_recovered'] for d in docs)} mã và "
             f"{sum(d['res']['stats']['cells_reocr'] for d in docs)} ô số được OCR lại riêng (400 dpi); "
             f"{stats.get('digit_suggested', 0)} gợi ý mới cho ô lệch đúng một chữ số so với kỳ liền kề "
             "(`suggested_by = adjacent_period`); "
             f"{stats.get('monthly_suggested', 0)} gợi ý mới từ đối soát báo cáo tháng (`suggested_by = monthly_crosscheck`).", "",
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
    lines += ["### Top 10 phương trình trượt nhiều nhất", ""] + _top_failures(docs)
    lines += ["### Phương trình cha–con: số báo cáo trượt", ""] + _parent_stats(docs)
    lines += ["### Đối soát B02 mã 10, 60 với báo cáo tháng lũy kế (thay vnstock)", "",
              "Số BCTC làm tròn về tỷ đồng có xuất hiện (±1 tỷ) trong báo cáo KQKD tháng 3/6/9/12 cùng kỳ lũy kế "
              "không (`src/quality/monthly_check.py`). Chỉ ghi lại, không ghi vào Gold.", ""]
    lines += monthly_report_lines(stats.get("monthly_xcheck", []))
    lines += ["### Kỳ bị trình bày lại (tự sinh)", "",
              "Mọi báo cáo năm N+1 (>= 2 báo cáo) in cùng một số đầu năm nhưng khác số cuối năm của BCTC năm N: "
              "coi là trình bày lại, không phải lỗi OCR, và không tính vào kiểm tra đầu năm = cuối năm trước.", ""]
    lines += _restated_table(stats["restated"])
    lines += ["### Chi tiết theo kỳ", ""]
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
        if d["label_note"]:
            notes.append(f"nhãn cột B02: {d['label_note']}")
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
