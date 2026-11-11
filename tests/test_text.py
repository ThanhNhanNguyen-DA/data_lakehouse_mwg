import unicodedata

from src.common.text import norm


def test_norm_bo_dau_va_chu_thuong():
    assert norm("BẢNG CÂN ĐỐI KẾ TOÁN HỢP NHẤT") == "bang can doi ke toan hop nhat"


def test_norm_chu_d():
    assert norm("Đầu tư Thế Giới Di Động") == "dau tu the gioi di dong"


def test_norm_gop_khoang_trang():
    assert norm("  Doanh thu \n\t thuần  ") == "doanh thu thuan"


def test_norm_chuoi_da_tach_dau_nfd():
    assert norm(unicodedata.normalize("NFD", "Lũy kế")) == "luy ke"


def test_norm_none_va_rong():
    assert norm(None) == ""
    assert norm("") == ""
