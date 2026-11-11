"""Test bộ dựng bảng bằng "từ + tọa độ" giả lập theo bố cục thật của BCTC MWG.

Mọi số tiền ở đây là số giả (1.111..., 2.222...), không phải số liệu của MWG.
"""
import csv
from datetime import date

import pytest
from PIL import Image

from src.extraction import ocr
from src.extraction.pdf_extractor import (
    apply_overrides, check_equations, classify_page, expected_end, find_dates, group_lines,
    load_overrides, locate_statement_pages, parse_amount, parse_statement_page, report_end_date,
)
from src.extraction.run_extraction import _check_period, _opening_check

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


@pytest.mark.parametrize("raw", ["26.1", "abc", "1.23.4"])
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

def _values(**cols):
    return {(st, code, kind): v for (st, code, kind), v in cols.items()}


def test_check_equations_dung_va_khoan_giam_tru_in_am():
    values = {("B01", "270", "closing"): 300, ("B01", "300", "closing"): 100, ("B01", "400", "closing"): 200,
              ("B02", "01", "current"): 1000, ("B02", "02", "current"): -10, ("B02", "10", "current"): 990,
              ("B02", "11", "current"): -900, ("B02", "20", "current"): 90}
    assert all(c["ok"] for c in check_equations(values))


def test_check_equations_sai_va_thieu_ma():
    values = {("B01", "270", "closing"): 310, ("B01", "300", "closing"): 100, ("B01", "400", "closing"): 200,
              ("B02", "10", "current"): 990}
    res = {c["check"]: c for c in check_equations(values)}
    assert not res["270 = 300 + 400"]["ok"] and "lệch" in res["270 = 300 + 400"]["note"]
    assert not res["10 = 01 - 02"]["ok"] and "thiếu" in res["10 = 01 - 02"]["note"]
    assert res["20 = 10 - 11"]["note"] == "thiếu mã 20"


def test_opening_check():
    doc = {"period": "2025-Q1", "cells": [
        {"statement": "B01", "period_kind": "opening", "account_code": "270", "amount_vnd": 500},
        {"statement": "B01", "period_kind": "opening", "account_code": "300", "amount_vnd": 200}]}
    assert _opening_check(doc, {2024: {"270": 500, "300": 200}})[0] is True
    assert _opening_check(doc, {2024: {"270": 500, "300": 999}})[0] is False
    assert _opening_check(doc, {})[0] is None


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

def test_ocr_text_thu_xoay_khi_trang_it_chu(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr, "OCR_CACHE", tmp_path)
    monkeypatch.setattr(ocr, "render", lambda path, page, dpi: Image.new("RGB", (40, 20), "white"))
    upright = " ".join(["doanh thu thuan"] * 30)

    calls = []

    def counting(img, lang):
        calls.append(img.size)
        return upright if len(calls) == 2 else "| . ,"  # chiều xoay thứ nhất (90 độ) là đúng
    monkeypatch.setattr(ocr.pytesseract, "image_to_string", counting)
    assert ocr.ocr_text(tmp_path / "x.pdf", "sha", 1) == upright
    assert ocr.page_rotation(tmp_path / "x.pdf", "sha", 1) == 90
    assert ocr.ocr_text(tmp_path / "x.pdf", "sha", 1) == upright and len(calls) == 4  # lần 2 đọc cache
