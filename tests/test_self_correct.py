"""Test tự sửa lỗi OCR và các cờ mới. Số tiền là số giả, không phải số liệu MWG."""
import csv
from datetime import date

import pytest

from src.extraction import run_extraction as rx
from src.extraction.pdf_extractor import check_q1_columns, eligible_equations, load_overrides, parse_statement_page
from src.extraction.self_correct import OVERRIDE_FIELDS, apply_consensus, ocr_like, suggest_fixes, write_suggestions
from tests.test_extraction import EQ, H, W, right


@pytest.mark.parametrize("a, b, expected", [
    (1234567, 1234567, True), (1234567, 1284567, True), (1234567, 1284561, True),
    (1234567, 9284561, False),            # sai 3 chữ số: không phải lỗi OCR (có thể là số trình bày lại)
    (1234567, 123456, False),              # khác số chữ số
    (-1234567, 1234567, True),             # mất dấu ngoặc
    (-1234567, 1284567, False),            # vừa khác dấu vừa sai chữ số
])
def test_ocr_like(a, b, expected):
    assert ocr_like(a, b) is expected


def _doc(period, doc_type, cells, sha=None):
    return {"period": period, "doc_type": doc_type, "sha256": sha or period, "cells": [
        {"statement": "B01", "account_code": code, "canonical_code": code, "period_kind": kind, "amount_vnd": v,
         "page": 1, "item_name": "", "raw_text": "", "is_manual": False} for code, kind, v in cells]}


def test_bo_phieu_sua_o_loi_ocr_va_dien_o_thieu():
    fy = _doc("2024", "FS_CONSOLIDATED_Y", [("270", "closing", 1284567), ("300", "closing", 500)])
    q1 = _doc("2025-Q1", "FS_CONSOLIDATED_Q", [("270", "opening", 1234567), ("300", "opening", 500)])
    h1 = _doc("2025-H1", "FS_CONSOLIDATED_H", [("270", "opening", 1234567)])  # thiếu 300
    fixed = apply_consensus([fy, q1, h1])
    assert fixed == 2
    c270 = next(c for c in fy["cells"] if c["account_code"] == "270")
    assert (c270["amount_vnd"], c270["corrected_by"], c270["ocr_value"]) == (1234567, "consensus", 1284567)
    added = next(c for c in h1["cells"] if c["account_code"] == "300")
    assert (added["amount_vnd"], added["period_kind"], added["is_consensus"]) == (500, "opening", True)
    assert all(c["is_consensus"] for c in q1["cells"])


def test_bo_phieu_khong_ghi_de_so_trinh_bay_lai():
    # cuối năm 2024 in 1.000.000; các báo cáo 2025 trình bày lại thành 2.345.678 (khác nhiều chữ số)
    fy = _doc("2024", "FS_CONSOLIDATED_Y", [("400", "closing", 1000000)])
    q1 = _doc("2025-Q1", "FS_CONSOLIDATED_Q", [("400", "opening", 2345678)])
    h1 = _doc("2025-H1", "FS_CONSOLIDATED_H", [("400", "opening", 2345678)])
    apply_consensus([fy, q1, h1])
    assert fy["cells"][0]["amount_vnd"] == 1000000 and "corrected_by" not in fy["cells"][0]


def test_bo_phieu_hoa_thi_khong_sua():
    a = _doc("2024", "FS_CONSOLIDATED_Y", [("270", "closing", 1234567)])
    b = _doc("2025-Q1", "FS_CONSOLIDATED_Q", [("270", "opening", 1284567)])
    assert apply_consensus([a, b]) == 0


def test_goi_y_sua_mot_o_theo_phuong_trinh():
    eq = {"regime": "R", "statement": "B01", "lhs": "270", "terms": [(1, "300"), (1, "400")], "label": "270 = 300 + 400"}
    values = {("B01", "270", "closing"): 1500, ("B01", "300", "closing"): 1000, ("B01", "400", "closing"): 800}
    failed = [{"equation": eq, "kind": "closing", "ok": False}]
    # 270 = 1500 và 400 = 800 đều có thể là ô sai (cần 1800 hoặc 500, đều lệch 1 chữ số) -> mơ hồ, không gợi ý
    assert suggest_fixes({}, failed, values, {}) == []
    # 270 đã được xác nhận khi bỏ phiếu giữa các lần in lại -> chỉ còn 400
    doc = {"cells": [{"statement": "B01", "account_code": "270", "period_kind": "closing", "is_consensus": True}]}
    s = suggest_fixes(doc, failed, values, {})
    assert [(x["account_code"], x["amount_vnd"], x["ocr_value"]) for x in s] == [("400", 500, 800)]


def test_goi_y_giu_dau_in_cho_khoan_giam_tru():
    eq = {"regime": "R", "statement": "B02", "lhs": "10", "terms": [(1, "01"), (-1, "02")], "label": "10 = 01 - 02"}
    values = {("B02", "10", "c"): 990, ("B02", "01", "c"): 1000, ("B02", "02", "c"): -40}
    # 10 và 01 đã đúng ở phương trình khác (vd. 20 = 10 - 11) -> chỉ còn 02; giữ dấu âm như in
    other = {"regime": "R", "statement": "B02", "lhs": "10", "terms": [(1, "01")], "label": "khác"}
    doc = {"checks": [{"equation": other, "kind": "c", "ok": True}]}
    s = suggest_fixes(doc, [{"equation": eq, "kind": "c", "ok": False}], values, {("B02", "02"): -1})
    assert [(x["account_code"], x["amount_vnd"]) for x in s] == [("02", -10)]


def test_ghi_goi_y_pending_va_khong_ap_dung(tmp_path):
    path = tmp_path / "manual_overrides.csv"
    with path.open("w", encoding="utf-8", newline="") as f:  # file cũ chưa có cột suggested_by/status
        csv.writer(f).writerow(OVERRIDE_FIELDS[:-2])
    doc = {"sha256": "abc", "period": "2025-Q3", "cells": [
        {"statement": "B01", "account_code": "400", "period_kind": "closing", "page": 7}]}
    sug = [{"statement": "B01", "account_code": "400", "period_kind": "closing", "amount_vnd": 300, "ocr_value": 800,
            "reason": "Phương trình 270 = 300 + 400: chỉ ô này sai"}]
    assert write_suggestions(path, doc, sug) == 1
    assert write_suggestions(path, doc, sug) == 0  # chạy lại không nhân dòng
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert (rows[0]["suggested_by"], rows[0]["status"], rows[0]["page"], rows[0]["fiscal_quarter"]) == (
        "equation", "pending", "7", "3")
    assert load_overrides(path) == {}  # pending: chưa áp dụng
    rows[0]["status"] = "confirmed"
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OVERRIDE_FIELDS)
        w.writeheader()
        w.writerows(rows)
    assert list(load_overrides(path)) == [("abc", "B01", "400", "closing")]


def test_phuong_trinh_danh_gia_duoc():
    assert [e["label"] for e in eligible_equations(EQ, {})] == ["50 = 30 + 40", "regime khác"]
    assert len(eligible_equations(EQ, {("B02", "02"): -1})) == 3


def test_q1_quy_nay_bang_luy_ke():
    v = {("B02", "01", "q_current"): 100, ("B02", "01", "ytd_current"): 100,
         ("B02", "10", "q_current"): 90, ("B02", "10", "ytd_current"): 95,
         ("B02", "01", "q_prior"): 80, ("B02", "01", "ytd_prior"): 80}
    res = {c["kind"]: c for c in check_q1_columns(v)}
    assert not res["q_current"]["ok"] and "10" in res["q_current"]["note"]
    assert res["q_prior"]["ok"]
    assert check_q1_columns({("B02", "01", "current"): 1}) == []


def test_parse_tra_dong_thieu_ma_va_o_sai_mau():
    words = [W("Mã", 40, 100), W("Thuyết", 425, 100), W("Số", 540, 100), W("cuối", 570, 100), W("năm", 610, 100),
             W("Số", 690, 100), W("đầu", 720, 100), W("năm", 760, 100),
             W("100", 40, 200), right("1.111.111.111", 640, 200), right("2.222.222.222", 790, 200),
             right("3.333.333.333", 640, 240), right("4.444.444444", 790, 240),  # mã 110 bị OCR làm mất
             W("120", 40, 280), right("5.555.555", 640, 280), right("6.666.666", 790, 280)]
    res = parse_statement_page(words, "B01")
    assert [r["account_code"] for r in res["rows"]] == ["100", "120"]
    assert len(res["orphans"]) == 1 and res["orphans"][0][0] == 240
    assert len(res["suspects"]) == 1 and res["suspects"][0][1] == 240


def test_reocr_fixes_them_ma_va_sua_o(monkeypatch):
    res = {"h": H, "code_band": (30, 80), "orphans": [(240, 260)], "suspects": [(673, 240, 790, 260)]}
    words = [right("4.444.444444", 790, 240)]
    calls = []

    def fake_region(path, sha, page, box, config):
        calls.append(config)
        return "110" if config == rx.ocr.REOCR_CODE else "4.444.444.444"
    monkeypatch.setattr(rx.ocr, "ocr_region", fake_region)
    out, n_codes, n_cells = rx._reocr_fixes(None, "sha", 1, words, res)
    assert (n_codes, n_cells) == (1, 1)
    assert sorted(w["text"] for w in out) == ["110", "4.444.444.444"]


@pytest.mark.parametrize("end, published, ok", [
    (date(2025, 9, 30), "2025-10-22", True), (date(2025, 12, 31), "2026-03-23", True),
    (date(2025, 4, 30), "2023-05-20", False),    # OCR đọc 2023 thành 2025: sau ngày đăng
    (date(2024, 12, 31), "2025-07-30", False),   # quá ~4 tháng trước ngày đăng
    (date(2025, 9, 30), None, True),
])
def test_ngay_doc_duoc_hop_ly(end, published, ok):
    assert rx.plausible_end(end, published) is ok


def test_ngay_kho_tin_giu_ky_theo_tieu_de():
    row = {"period": "2023-M04", "doc_type": "MONTHLY_RESULT", "period_candidates": None, "published": "2023-05-20"}
    match = lambda per, end: rx._month_end(per) == end  # noqa: E731
    assert rx._period_result(row, rx._month_end("2025-M04"), match) == {
        "period_check_ok": True, "ocr_date_suspect": True, "resolved": None}
    assert rx._period_result(row, rx._month_end("2023-M04"), match)["ocr_date_suspect"] is False


def test_regime_suy_ra_theo_nam():
    ctx = {"overrides": {}, "regime_map": {}}
    res = {"regime": None, "cells": [], "end": None}
    row = {"period": "2021-Q3", "doc_type": "FS_CONSOLIDATED_Q", "period_candidates": None, "published": None,
           "sha256": "x"}
    d = rx._make_doc(row, res, ctx)
    assert (d["regime"], d["regime_inferred"]) == ("TT200", True)
    d = rx._make_doc({**row, "period": "2026-Q3"}, res, ctx)
    assert (d["regime"], d["regime_inferred"]) == (None, False)


def test_cot_b02_tt99_khong_doc_duoc_tieu_de_thi_gan_co():
    ctx = {"dim": [], "equations": [], "regime_map": {}}
    d = {"regime": "TT99", "period": "2026-Q3", "cells": [], "res": {"b02_header_ok": False}}
    rx._evaluate(d, ctx)
    assert d["columns_ok"] is False and d["dq_balance_ok"] is False
    d = {"regime": "TT200", "period": "2025-Q3", "cells": [], "res": {"b02_header_ok": False}}
    rx._evaluate(d, ctx)
    assert d["columns_ok"] is True
