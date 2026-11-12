"""Test bộ dựng bảng bằng "từ + tọa độ" giả lập theo bố cục thật của BCTC MWG.

Mọi số tiền ở đây là số giả (1.111..., 2.222...), không phải số liệu của MWG.
"""
import csv
from datetime import date

import pytest
from PIL import Image

from src.extraction import ocr
from src.extraction.pdf_extractor import (
    apply_overrides, check_equations, check_parent_sums, classify_page, detect_regime, expected_end,
    find_dates, group_lines, load_equations, load_overrides, locate_statement_pages, parse_amount,
    parse_statement_page, report_end_date, signs_of,
)
from src.extraction import run_extraction
from src.extraction.run_extraction import _check_period, _opening_check, canonical

H = 20  # chiều cao chữ (px)


def W(text, x0, y, width=None):
    width = width or max(len(text) * 9, 9)
    return {"text": text, "x0": x0, "y0": y, "x1": x0 + width, "y1": y + H}


def right(text, x1, y):
    """Số căn phải: mép phải tại x1."""
    width = len(text) * 9
    return W(text, x1 - width, y, width)


# ---------- Số tiền ----------

@pytest.mark.parametrize("raw, expected", [
    ("1.111.111.111", 1111111111), ("2,222,222,222", 2222222222), ("(3.333.333)", -3333333),
    ("(4,444,444", -4444444), ("-5.555", -5555), ("123", 123), ("|6.666.666|", 6666666),
])
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == expected


@pytest.mark.parametrize("raw", ["-", "–", "(-)"])
def test_parse_amount_null(raw):
    assert parse_amount(raw) is None


@pytest.mark.parametrize("raw, expected", [
    ("90.899.467872", 90899467872), ("2,.430.887", 2430887), ("5,788,331-", 5788331),
    ("27,29(9,659,741)", -9659741),
])
def test_parse_amount_de_tinh_voi_dau_phan_cach_ocr(raw, expected):
    assert parse_amount(raw) == expected


@pytest.mark.parametrize("raw", ["abc", "1.2B4", ".", "7.994.614.245.183.375.265.762.233.874"])
def test_parse_amount_sai(raw):
    with pytest.raises(ValueError):
        parse_amount(raw)


def test_group_lines_theo_truc_y():
    words = [W("b", 200, 103), W("a", 10, 100), W("c", 10, 140)]
    assert [[w["text"] for w in line] for line in group_lines(words)] == [["a", "b"], ["c"]]


def test_group_lines_dung_line_key_cua_tesseract():
    words = [{**W("x", 10, 100), "line_key": "1"}, {**W("y", 300, 130), "line_key": "1"},
             {**W("z", 10, 160), "line_key": "2"}]
    assert [[w["text"] for w in line] for line in group_lines(words)] == [["x", "y"], ["z"]]


# ---------- Bố cục BCTC năm (vi): Mã số | Tài sản | Thuyết minh | Số cuối năm | Số đầu năm ----------

def _vi_annual_b01():
    c1, c2 = 640, 790
    return [
        W("Mã", 40, 100), W("số", 42, 122), W("TÀI", 100, 110), W("SẢN", 140, 110),
        W("Thuyết", 425, 100), W("minh", 430, 122),
        W("Số", 520, 110), W("cuối", 550, 110), W("năm", 595, 110),
        W("Số", 670, 110), W("đầu", 700, 110), W("năm", 745, 110),
        W("100", 40, 200), W("A.", 100, 200), W("TÀI", 130, 200), right("1.111.111.111", c1, 200),
        right("2.222.222.222", c2, 200),
        # tên xuống dòng: mã ở dòng đầu, số tiền ở dòng cuối
        W("110", 40, 240), W("Tiền", 100, 240), W("và", 140, 240),
        W("tương", 110, 262), W("đương", 165, 262), W("5", 440, 262), right("333.333.333", c1, 262),
        right("(444.444.444)", c2, 262),
        W("111", 40, 290), W("Tiền", 110, 290), right("-", c1, 290), right("555.555", c2, 290),
    ]


def test_bo_cuc_nam_vi_ma_ben_trai_ten_xuong_dong():
    res = parse_statement_page(_vi_annual_b01(), "B01")
    assert res["kinds"] == ["closing", "opening"] and res["header_ok"]
    rows = {r["account_code"]: r["values"] for r in res["rows"]}
    assert rows == {"100": [1111111111, 2222222222], "110": [333333333, -444444444], "111": [None, 555555]}
    assert "tương" in next(r["item_name"] for r in res["rows"] if r["account_code"] == "110")


def test_trang_tiep_theo_khong_co_tieu_de_cot_dung_lai_trang_truoc():
    first = parse_statement_page(_vi_annual_b01(), "B01")
    cont = [W("300", 40, 100), W("C.", 100, 100), right("6.666.666", 640, 100), right("7.777.777", 790, 100),
            W("400", 40, 140), W("D.", 100, 140), right("8.888.888", 640, 140), right("9.999.999", 790, 140)]
    res = parse_statement_page(cont, "B01", prev=first)
    assert res["kinds"] == ["closing", "opening"]
    assert {r["account_code"]: r["values"] for r in res["rows"]} == {
        "300": [6666666, 7777777], "400": [8888888, 9999999]}


# ---------- BCTC quý, B02 (vi): STT | Chỉ tiêu | Mã số | Thuyết minh | 4 cột ----------

def test_b02_quy_ma_o_giua_so_thu_tu_ben_trai_bon_cot():
    xs = [560, 700, 840, 980]
    heads = [("Số quý này năm nay", 440), ("Số lũy kế năm nay", 580), ("Số quý này năm trước", 720),
             ("Số lũy kế năm trước", 860)]  # tiêu đề nằm gọn trong cột (mép phải cột: xs)
    words = [W("CHỈ", 60, 100), W("TIÊU", 95, 100), W("Mã", 300, 95), W("số", 302, 117),
             W("Thuyết", 345, 95), W("minh", 350, 117)]
    for text, x0 in heads:
        words += [W(t, x0 + i * 24, 110, 22) for i, t in enumerate(text.split())]
    words += [W("1", 20, 200), W("Doanh", 40, 200), W("thu", 100, 200), W("1", 305, 200), W("24.1", 350, 200),
              *[right(v, x, 215) for v, x in zip(["1.000.000", "3.000.000", "900.000", "2.700.000"], xs)],
              W("2", 20, 260), W("Giảm", 40, 260), W("2", 305, 260),
              *[right(v, x, 260) for v, x in zip(["(10.000)", "(30.000)", "(9.000)", "(27.000)"], xs)],
              W("3", 20, 300), W("Thuần", 40, 300), W("10", 300, 300),
              *[right(v, x, 300) for v, x in zip(["990.000", "2.970.000", "891.000", "2.673.000"], xs)]]
    res = parse_statement_page(words, "B02")
    assert res["kinds"] == ["q_current", "ytd_current", "q_prior", "ytd_prior"] and res["header_ok"]
    rows = {r["account_code"]: r["values"] for r in res["rows"]}
    assert set(rows) == {"01", "02", "10"}  # "1" -> "01"; số thứ tự bên trái không bị nhận là mã
    assert rows["02"] == [-10000, -30000, -9000, -27000]


# ---------- BCTC năm (en): Code | Assets | Notes | Ending balance | Beginning balance ----------

def test_bo_cuc_en_dau_phay_va_gach_ngang():
    words = [W("Code", 40, 100), W("ASSETS", 100, 100), W("Notes", 430, 100),
             W("Ending", 540, 100), W("balance", 590, 100), W("Beginning", 680, 100), W("balance", 750, 100),
             W("120", 40, 200), W("II.", 100, 200), right("5,555,555", 640, 200), right("7,777,777", 790, 200),
             W("123", 40, 230), W("1.", 110, 230), W("6", 440, 230), right("5,555,555", 640, 230), right("-", 790, 230)]
    res = parse_statement_page(words, "B01")
    assert res["kinds"] == ["closing", "opening"]
    assert {r["account_code"]: r["values"] for r in res["rows"]} == {"120": [5555555, 7777777], "123": [5555555, None]}


# ---------- Tìm trang báo cáo ----------

def _page(top, codes=True):
    return top + ("\n" + "\n".join(f"{c} chi tieu 1.111.111" for c in (100, 110, 120, 130, 140, 150)) if codes else "")


def test_locate_statement_pages_bo_muc_luc_va_dung_o_thuyet_minh():
    pages = {
        1: "Công ty Cổ phần Đầu tư Thế Giới Di Động\nBáo cáo tài chính hợp nhất",
        2: "MỤC LỤC\nBảng cân đối kế toán hợp nhất 5\nBáo cáo kết quả hoạt động kinh doanh hợp nhất 7\n"
           "Báo cáo lưu chuyển tiền tệ hợp nhất 8",
        3: "BÁO CÁO KIỂM TOÁN ĐỘC LẬP\nbảng cân đối kế toán hợp nhất, báo cáo kết quả hoạt động kinh doanh hợp nhất",
        4: _page("Công ty Cổ phần Đầu tư Thế Giới Di Động B01-DN/HN\nBẢNG CÂN ĐỐI KẾ TOÁN HỢP NHẤT"),
        5: _page("B01-DN/HN\nBẢNG CÂN ĐỐI KẾ TOÁN HỢP NHẤT (tiếp theo)"),
        6: _page("B02a-DN/HN\nBÁO CÁO KẾT QUẢ HOẠT ĐỘNG KINH DOANH HỢP NHẤT GIỮA NIÊN ĐỘ"),
        7: _page("B03-DN/HN\nBÁO CÁO LƯU CHUYỂN TIỀN TỆ HỢP NHẤT"),
        8: _page("BÁO CÁO LƯU CHUYỂN TIỀN TỆ HỢP NHẤT (tiếp theo)"),
        9: "B09-DN/HN\nTHUYẾT MINH BÁO CÁO TÀI CHÍNH HỢP NHẤT\n1. Thông tin công ty",
        10: _page("B01-DN/HN"),  # không được duyệt tới
    }
    seen = []

    def get_text(p):
        seen.append(p)
        return pages[p]

    assert locate_statement_pages(get_text, 10) == {"B01": [4, 5], "B02": [6], "B03": [7, 8]}
    assert 10 not in seen


def test_classify_page_en():
    assert classify_page(_page("B02-DN/HN\nCONSOLIDATED INCOME STATEMENT"))["statement"] == "B02"
    assert classify_page("B09-DN/HN\nNOTES TO THE CONSOLIDATED FINANCIAL STATEMENTS")["is_notes"]


# ---------- Ngày báo cáo ----------

def test_find_dates_va_ngay_ket_thuc_ky():
    text = ("BẢNG CÂN ĐỐI KẾ TOÁN HỢP NHẤT\nvào ngày 31 tháng 12 năm 2025\nSố đầu năm 01/01/2025\n"
            "Ngày 23 tháng 3 năm 2026")  # ngày ký không phải cuối quý -> bỏ
    assert date(2026, 3, 23) in find_dates(text)
    assert report_end_date(text) == date(2025, 12, 31)
    assert report_end_date("CONSOLIDATED BALANCE SHEET as at 31 December 2018\n31 December 2017") == date(2018, 12, 31)
    assert report_end_date("for the period ended September 30, 2019") == date(2019, 9, 30)
    assert report_end_date("không có ngày") is None


@pytest.mark.parametrize("period, expected", [
    ("2025", date(2025, 12, 31)), ("2025-Q1", date(2025, 3, 31)), ("2025-H1", date(2025, 6, 30)),
    ("2025-Q3", date(2025, 9, 30)), ("pending", None), ("2025-M05", None),
])
def test_expected_end(period, expected):
    assert expected_end(period) == expected


def test_check_period_pending_chon_dung_ung_vien():
    row = {"period": "pending", "doc_type": "PENDING",
           "period_candidates": "FS_CONSOLIDATED_H:2017-H1;FS_CONSOLIDATED_Q:2017-Q3"}
    match = lambda p, d: d is not None and expected_end(p) == d  # noqa: E731
    assert _check_period(row, date(2017, 9, 30), match) == (True, ("FS_CONSOLIDATED_Q", "2017-Q3"))
    assert _check_period(row, date(2017, 12, 31), match) == (False, None)
    assert _check_period({"period": "2017-Q3", "doc_type": "FS_CONSOLIDATED_Q", "period_candidates": None},
                         None, match) == (False, None)


# ---------- DQ ----------

# Phương trình giả để test (cùng dạng config/dq_equations.csv)
EQ = [{"regime": "R", "statement": "B02", "lhs": "10", "terms": [(1, "01"), (-1, "02")], "label": "10 = 01 - 02"},
      {"regime": "R", "statement": "B02", "lhs": "50", "terms": [(1, "30"), (1, "40")], "label": "50 = 30 + 40"},
      {"regime": "X", "statement": "B01", "lhs": "999", "terms": [(1, "1")], "label": "regime khác"}]


def test_check_equations_chi_dung_phuong_trinh_cua_regime():
    values = {("B02", "01", "c"): 1000, ("B02", "02", "c"): 10, ("B02", "10", "c"): 990,
              ("B02", "30", "c"): 100, ("B02", "40", "c"): -30, ("B02", "50", "c"): 70}
    res = check_equations(values, EQ, "R")
    assert [c["check"] for c in res] == ["10 = 01 - 02", "50 = 30 + 40"] and all(c["ok"] for c in res)


def test_tri_tuyet_doi_chi_cho_ma_sign_am():
    printed_negative = {("B02", "01", "c"): 1000, ("B02", "02", "c"): -10, ("B02", "10", "c"): 990,
                        ("B02", "30", "c"): 100, ("B02", "40", "c"): -30, ("B02", "50", "c"): 70}
    # chưa khai báo sign: giữ đúng dấu in -> 01 - (-10) = 1010 != 990
    assert not check_equations(printed_negative, EQ, "R")[0]["ok"]
    # 02 là khoản giảm trừ (sign = -1): luôn trừ |02|; 40 (lỗ khác) không có sign -1 nên giữ số âm
    res = check_equations(printed_negative, EQ, "R", signs={("B02", "02"): -1})
    assert all(c["ok"] for c in res)


def test_check_equations_sai_va_thieu_ma():
    res = check_equations({("B02", "01", "c"): 1000, ("B02", "02", "c"): 10, ("B02", "10", "c"): 900}, EQ, "R")
    assert not res[0]["ok"] and "lệch" in res[0]["note"]
    assert res[1]["note"] == "thiếu mã 50"


def test_dq_equations_csv_doc_duoc():
    eqs = load_equations(run_extraction.EQUATIONS)
    assert {e["regime"] for e in eqs} == {"TT200", "TT99"}
    assert all(e["terms"] for e in eqs)


def test_check_parent_sums_theo_dim_account():
    dim = [{"regime": "R", "statement": "B01", "account_code": "140", "parent_code": "", "is_additive": "", "sign": "1"},
           {"regime": "R", "statement": "B01", "account_code": "141", "parent_code": "140", "is_additive": "1", "sign": "1"},
           {"regime": "R", "statement": "B01", "account_code": "149", "parent_code": "140", "is_additive": "1", "sign": "-1"}]
    values = {("B01", "140", "closing"): 90, ("B01", "141", "closing"): 100, ("B01", "149", "closing"): -10}
    assert signs_of(dim, "R") == {("B01", "140"): 1, ("B01", "141"): 1, ("B01", "149"): -1}
    assert [c["ok"] for c in check_parent_sums(values, dim, "R")] == [True]
    assert check_parent_sums(values, dim, "khác") == []
    # một dòng con chưa chắc (is_additive trống) -> không kiểm dòng tổng đó
    dim.append({"regime": "R", "statement": "B01", "account_code": "148", "parent_code": "140", "is_additive": "",
                "sign": ""})
    assert check_parent_sums(values, dim, "R") == []


def test_detect_regime_theo_ten_b01():
    assert detect_regime("BÁO CÁO TÌNH HÌNH TÀI CHÍNH HỢP NHẤT GIỮA NIÊN ĐỘ") == "TT99"
    assert detect_regime("BẢNG CÂN ĐỐI KẾ TOÁN HỢP NHẤT") == "TT200"
    assert detect_regime("CONSOLIDATED BALANCE SHEET") == "TT200"
    assert detect_regime("không có tiêu đề") is None
    assert classify_page(_page("BÁO CÁO TÌNH HÌNH TÀI CHÍNH HỢP NHẤT"))["statement"] == "B01"


def test_ma_chuan_va_doi_chieu_dau_nam_qua_regime():
    regime_map = {("TT99", "B01", "280"): "270"}
    assert canonical("TT200", "B01", "270", regime_map) == "270"
    assert canonical("TT99", "B01", "280", regime_map) == "270"
    assert canonical("TT99", "B01", "142", regime_map) is None  # chưa map -> không so
    doc = {"period": "2026-Q3", "regime": "TT99", "cells": [
        {"statement": "B01", "period_kind": "opening", "account_code": "280", "canonical_code": "270", "amount_vnd": 5}]}
    assert _opening_check(doc, {2025: {"270": 5}})[0] is True
    doc["cells"][0]["canonical_code"] = None
    assert _opening_check(doc, {2025: {"270": 5}}) == (None, "chờ account_code_regime_map cho TT99")


def test_cot_tt99_quy_iii_va_luy_ke():
    xs = [560, 700, 840, 980]
    words = [W("CHỈ", 60, 100), W("TIÊU", 95, 100), W("Mã", 300, 95), W("số", 302, 117),
             W("Thuyết", 345, 95), W("minh", 350, 117),
             W("Quý", 560, 80), W("III", 600, 80), W("Lũy", 820, 80), W("kế", 860, 80), W("từ", 890, 80),
             W("Năm", 470, 110), W("nay", 505, 110), W("Năm", 610, 110), W("trước", 645, 110),
             W("Năm", 750, 110), W("nay", 785, 110), W("Năm", 890, 110), W("trước", 925, 110),
             W("1.", 20, 200), W("Doanh", 40, 200), W("01", 300, 200),
             *[right(v, x, 200) for v, x in zip(["1.000.000", "900.000", "3.000.000", "2.700.000"], xs)]]
    res = parse_statement_page(words, "B02")
    assert res["kinds"] == ["q_current", "q_prior", "ytd_current", "ytd_prior"]


def test_thay_so_tien_bang_ocr_tung_cot(monkeypatch):
    words = [W("100", 40, 200), W("A.", 100, 200), right("1.111.1B1.111", 640, 200), right("2.222.222.222", 790, 200)]
    words[0] = W("1OO", 40, 200)  # mã OCR sai khi đọc cả trang
    res = {"h": H, "table_top": 200, "bottom": 220, "bounds": [500, 640], "cols": [640, 790],
           "col_widths": [117, 117], "code_band": (30, 80)}
    seen = []

    def fake_columns(path, sha, page, boxes, config=None):
        seen.append((boxes, config))
        if config:  # cột mã
            return [[W("100", 40, 200)]]
        return [[right("1.111.111.111", 640, 200)], [right("2.222.222.222", 790, 200)]]
    monkeypatch.setattr(run_extraction.ocr, "ocr_columns", fake_columns)
    out = run_extraction._with_column_ocr(None, "sha", 1, words, res)
    assert sorted(w["text"] for w in out) == ["1.111.111.111", "100", "2.222.222.222", "A."]
    assert len(seen[0][0]) == 2 and seen[0][0][0][0] > 500 and seen[1][1] == run_extraction.ocr.CODE_CONFIG


def test_apply_overrides_truoc_khi_chon_ban_thang(tmp_path):
    path = tmp_path / "manual_overrides.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "statement", "fiscal_year", "fiscal_quarter", "period_kind", "account_code",
                    "amount_vnd", "ocr_value", "source_doc_sha256", "page", "reason", "entered_by", "entered_at"])
        w.writerow(["MWG", "B01", "2025", "0", "closing", "270", "300", "308", "abc", "7", "OCR đọc 0 thành 8",
                    "tester", "2026-11-11"])
    rows = [{"source_doc_sha256": "abc", "statement": "B01", "account_code": "270", "period_kind": "closing",
             "amount_vnd": 308},
            {"source_doc_sha256": "abc", "statement": "B01", "account_code": "300", "period_kind": "closing",
             "amount_vnd": 100}]
    out = apply_overrides(rows, load_overrides(path))
    assert [(r["amount_vnd"], r["is_manual"]) for r in out] == [(300, True), (100, False)]


# ---------- OCR: trang in ngang ----------

def test_ocr_text_xoay_theo_osd_va_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr, "OCR_CACHE", tmp_path)
    monkeypatch.setattr(ocr, "render", lambda path, page, dpi: Image.new("RGB", (40, 20), "white"))
    monkeypatch.setattr(ocr.pytesseract, "image_to_osd",
                        lambda img, output_type: {"rotate": 90, "orientation_conf": 7.5})
    sizes = []
    monkeypatch.setattr(ocr.pytesseract, "image_to_string", lambda img, lang: sizes.append(img.size) or "chu")
    assert ocr.ocr_text(tmp_path / "x.pdf", "sha", 1) == "chu"
    assert sizes == [(20, 40)]  # đã xoay trước khi OCR
    assert ocr.page_rotation(tmp_path / "x.pdf", "sha", 1) == 270  # PIL xoay ngược chiều kim đồng hồ
    ocr.ocr_text(tmp_path / "x.pdf", "sha", 1)
    assert len(sizes) == 1  # lần 2 đọc cache


def test_osd_tin_cay_thap_thi_khong_xoay(monkeypatch):
    monkeypatch.setattr(ocr, "render", lambda path, page, dpi: Image.new("RGB", (40, 20), "white"))
    monkeypatch.setattr(ocr.pytesseract, "image_to_osd",
                        lambda img, output_type: {"rotate": 180, "orientation_conf": 0.3})
    assert ocr.detect_rotation(None, 1) == 0


def test_tong_dong_con_coi_o_gach_ngang_la_0():
    dim = [{"regime": "R", "statement": "B01", "account_code": c, "parent_code": "410", "is_additive": "1", "sign": "1"}
           for c in ("411", "417")]
    values = {("B01", "410", "closing"): 100, ("B01", "411", "closing"): 100, ("B01", "417", "closing"): None,
              ("B01", "417", "opening"): 5, ("B01", "411", "opening"): 95, ("B01", "410", "opening"): 100}
    assert all(c["ok"] for c in check_parent_sums(values, dim, "R"))


def test_o_mot_hai_chu_so_la_dang_ngo():
    words = [W("Mã", 40, 100), W("Thuyết", 425, 100), W("Số", 540, 100), W("cuối", 570, 100), W("năm", 610, 100),
             W("Số", 690, 100), W("đầu", 720, 100), W("năm", 760, 100),
             W("100", 40, 200), right("1.111.111.111", 640, 200), right("2.222.222.222", 790, 200),
             W("440", 40, 240), right("3", 640, 240), right("4.444.444.444", 790, 240)]
    res = parse_statement_page(words, "B01")
    assert len(res["suspects"]) == 1 and res["suspects"][0][1] == 240


def test_ma_tuy_chon_khong_in_tinh_la_0():
    eq = {"regime": "R", "statement": "B02", "lhs": "30", "rhs": "20+24-25", "label": "30",
          "terms": [(1, "20"), (1, "24"), (-1, "25")], "optional": {"24"}}
    values = {("B02", "20", "c"): 100, ("B02", "25", "c"): -30, ("B02", "30", "c"): 70}
    assert check_equations(values, [eq], "R", {("B02", "25"): -1})[0]["ok"]
    # vế không tùy chọn vẫn là thiếu
    del values[("B02", "25", "c")]
    assert check_equations(values, [eq], "R", {("B02", "25"): -1})[0]["note"] == "thiếu 25"


def test_critical_columns_chan_b02_theo_cot():
    from src.extraction.run_extraction import critical_columns
    eq = {"statement": "B02", "lhs": "10"}
    d = {"res": {"pages": {"B02": [5]}}, "period_check_ok": True, "columns_ok": True,
         "checks": [{"level": "critical", "equation": eq, "kind": "q_current", "ok": True},
                    {"level": "critical", "equation": {**eq, "lhs": "20"}, "kind": "q_current", "ok": True},
                    {"level": "critical", "equation": eq, "kind": "ytd_current", "ok": False},
                    {"level": "warning", "equation": {**eq, "lhs": "22"}, "kind": "q_prior", "ok": True}]}
    assert critical_columns(d) == {"q_current": True, "ytd_current": False}
    assert critical_columns({**d, "columns_ok": False}) == {}


def test_monthly_suggestions_chi_goi_y_khi_doc_lai_khop(monkeypatch):
    from src.extraction import run_extraction as R
    cell = {"account_code": "10", "period_kind": "current", "amount_vnd": 73_755_000_000_000, "page": 9}
    rows = [{"found": False, "nearest": 73_655, "billion": 73_755, "month": "2025-M06", "doc": {}, "cell": cell},
            {"found": False, "nearest": 80_000, "billion": 73_755, "month": "2025-M06", "doc": {}, "cell": cell},
            {"found": True, "nearest": 73_755, "billion": 73_755, "month": "2025-M06", "doc": {}, "cell": cell}]
    monkeypatch.setattr(R, "reread_cell", lambda d, c, accept: next((v for v in (73_655_100_000_000,) if accept(v)), None))
    out = R.monthly_suggestions(rows)
    assert len(out) == 1 and out[0][1]["amount_vnd"] == 73_655_100_000_000 and out[0][1]["ocr_value"] == cell["amount_vnd"]
    monkeypatch.setattr(R, "reread_cell", lambda d, c, accept: None)
    assert R.monthly_suggestions(rows) == []


def _b02_doc(period, values, method="positional"):
    cells = [{"statement": "B02", "account_code": "10", "canonical_code": "10", "period_kind": k, "amount_vnd": v}
             for k, v in values.items()]
    return {"period": period, "b02_label_method": method, "cells": cells, "label_note": ""}


def test_check_positional_order_doi_thu_tu_cot_cu():
    from src.extraction.run_extraction import check_positional_order
    # thứ tự cột cũ: quý này, quý năm trước, lũy kế, lũy kế năm trước -> nhãn vị trí gán sai
    d = _b02_doc("2016-Q3", {"q_current": 110, "ytd_current": 66, "q_prior": 300, "ytd_prior": 175})
    assert check_positional_order(d) is True and d["b02_label_method"] == "numeric"
    got = {c["period_kind"]: c["amount_vnd"] for c in d["cells"]}
    assert got == {"q_current": 110, "q_prior": 66, "ytd_current": 300, "ytd_prior": 175}
    ok = _b02_doc("2024-Q3", {"q_current": 34, "ytd_current": 99, "q_prior": 30, "ytd_prior": 86})
    assert check_positional_order(ok) is False and ok["b02_label_method"] == "positional"
    q1 = _b02_doc("2024-Q1", {"q_current": 5, "ytd_current": 5, "q_prior": 4, "ytd_prior": 4})
    assert check_positional_order(q1) is False
