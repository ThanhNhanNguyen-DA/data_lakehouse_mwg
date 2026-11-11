import pytest

from src.extraction.pdf_extractor import to_number, unit_multiplier


@pytest.mark.parametrize("raw, expected", [
    ("1.234.567", 1234567.0),
    ("123", 123.0),
    ("(1.234)", -1234.0),
    ("( 1.234.567 )", -1234567.0),
    ("-1.234", -1234.0),
    ("−1.234", -1234.0),
    ("12,5", 12.5),
    ("1.234,56", 1234.56),
    ("1 234 567", 1234567.0),
    ("0", 0.0),
])
def test_to_number_kieu_viet_nam(raw, expected):
    assert to_number(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "  ", "-", "–", "()", "(-)"])
def test_to_number_rong_la_null(raw):
    assert to_number(raw) is None


def test_to_number_kieu_tieng_anh():
    assert to_number("1,234,567", thousands=",", decimal=".") == 1234567.0
    assert to_number("(1,234.5)", thousands=",", decimal=".") == -1234.5


@pytest.mark.parametrize("raw", ["1.23.4", "abc", "1.2345", "12a"])
def test_to_number_sai_dinh_dang_bao_loi(raw):
    with pytest.raises(ValueError):
        to_number(raw)


@pytest.mark.parametrize("text, expected", [
    ("Mẫu số B 01 - DN/HN\nĐơn vị tính: VND", 1),
    ("Đơn vị tính: triệu đồng", 1_000_000),
    ("Đơn vị: Nghìn đồng", 1_000),
    ("Đơn vị tính : tỷ đồng", 1_000_000_000),
    ("Unit: VND million", 1_000_000),
    ("Unit: VND", 1),
    ("Đơn vị tính: VND   Công ty CP Đầu tư Thế Giới Di Động", 1),
    ("Bảng cân đối kế toán hợp nhất", None),
])
def test_unit_multiplier(text, expected):
    assert unit_multiplier(text) == expected
