"""Tiêu đề lấy nguyên văn từ API mwg.vn (khảo sát 11/11/2026), trừ khi ghi chú khác."""
import pytest

from src.ingestion.mwg_crawler import (
    assurance_level, classify, load_config, parse_period, unfilled_fields,
)

REAL_TITLES = [
    # (tiêu đề, doc_type, kỳ, mức tin cậy)
    ("Báo Cáo Tài Chính Hợp Nhất Quý 3 Năm 2026", "FS_CONSOLIDATED_Q", "2026-Q3", 1),
    ("Báo Cáo Tài Chính Hợp Nhất Soát Xét Bán Niên Năm 2026", "FS_CONSOLIDATED_H", "2026-H1", 2),
    ("Báo Cáo Tài Chính Hợp Nhất Soát Xét Bán Niên 2025", "FS_CONSOLIDATED_H", "2025-H1", 2),
    ("Báo Cáo Tài Chính Hợp Nhất Kiểm Toán Năm 2025", "FS_CONSOLIDATED_Y", "2025", 3),
    ("Báo Cáo Tài Chính Hợp Nhất Quý 4 Năm 2025", "FS_CONSOLIDATED_Q", "2025-Q4", 1),
    ("Báo Cáo Tài Chính Hợp Nhất Soát Xét Năm 2020", "FS_CONSOLIDATED_Y", "2020", 3),
    ("Báo Cáo Tài Chính Hợp Nhất Soát Xét Qúy 3 Năm 2020", "FS_CONSOLIDATED_Q", "2020-Q3", 2),
    ("BCTC Hợp Nhất Đã Kiểm Toán 6 Tháng Đầu Năm 2020", "FS_CONSOLIDATED_H", "2020-H1", 3),
    ("Báo Cáo Tài Chính Soát Xét Hợp Nhất Quý 1/2020", "FS_CONSOLIDATED_Q", "2020-Q1", 2),
    ("Báo cáo tài chính hợp nhất năm 2014", "FS_CONSOLIDATED_Y", "2014", 3),
    ("Báo cáo tài chính hợp nhất 6 tháng đầu năm 2014", "FS_CONSOLIDATED_H", "2014-H1", 2),
    ("Báo cáo tài chính Quý III năm 2014", "FS_CONSOLIDATED_Q", "2014-Q3", 1),
    ("Cập Nhật Tình Hình Kinh Doanh 9 Tháng 2026", "MONTHLY_RESULT", "2026-M09", 1),
    ("Cập nhật tình hình kinh doanh 12 tháng 2025", "MONTHLY_RESULT", "2025-M12", 1),
    ("Báo cáo tóm tắt KQKD 8 tháng đầu năm 2017", "MONTHLY_RESULT", "2017-M08", 1),
    ("Báo cáo tóm tắt KQKD tháng 1 năm 2017", "MONTHLY_RESULT", "2017-M01", 1),
    ("Báo cáo tóm tắt KQKD cả năm 2017", "MONTHLY_RESULT", "2017-M12", 1),
    ("Báo cáo tài chính hợp nhất soát xét QIII năm 2017", "FS_CONSOLIDATED_Q", "2017-Q3", 2),
    ("Báo cáo tài chính hợp nhất soát xét QII năm 2017", "FS_CONSOLIDATED_Q", "2017-Q2", 2),
    ("Báo cáo tài chính hợp nhất (đã kiểm toán) năm 2016", "FS_CONSOLIDATED_Y", "2016", 3),
    ("Báo cáo tài chính hợp nhất (đã soát xét) quý III năm 2016", "FS_CONSOLIDATED_Q", "2016-Q3", 2),
    ("Báo cáo tài chính hợp nhất bán niên năm 2015", "FS_CONSOLIDATED_H", "2015-H1", 2),
    ("Báo cáo KQKD cả năm 2021", "MONTHLY_RESULT", "2021-M12", 1),
    ("Báo cáo tóm tắt KQKD mùa Tết (tháng 1+2) năm 2022", "MONTHLY_RESULT", "2022-M02", 1),
    ("Báo cáo tóm tắt KQKD tháng 1 đầu năm 2021", "MONTHLY_RESULT", "2021-M01", 1),
]

# Tiêu đề thật không ghi năm: lấy năm từ danh mục (category_year)
REAL_TITLES_NO_YEAR = [
    ("Báo cáo tài chính hợp nhất soát xét Quý III", 2018, "FS_CONSOLIDATED_Q", "2018-Q3"),
    ("Báo cáo tài chính hợp nhất soát xét bán niên", 2018, "FS_CONSOLIDATED_H", "2018-H1"),
    ("Cập nhật tình hình kinh doanh 6 tháng đầu năm", 2024, "MONTHLY_RESULT", "2024-M06"),
    ("Báo cáo tóm tắt KQKD 7 tháng đầu năm", 2022, "MONTHLY_RESULT", "2022-M07"),
]


@pytest.mark.parametrize("title, year, doc_type, period", REAL_TITLES_NO_YEAR)
def test_tieu_de_that_khong_ghi_nam(title, year, doc_type, period):
    assert classify(title) == doc_type
    assert parse_period(title, doc_type) is None
    assert parse_period(title, doc_type, default_year=year) == period

REAL_SKIPPED = [
    "Báo Cáo Tài Chính Riêng Quý 3 Năm 2026",
    "Báo Cáo Tài Chính Riêng Soát Xét Bán Niên Năm 2026",
    "Báo Cáo Tài Chính Riêng Kiểm Toán Năm 2025",
    "Báo Cáo Tài Chính Soát Xét Riêng Quý 1/2020",
    "BCTC Riêng Đã Kiểm Toán 6 Tháng Đầu Năm 2020",
    "Báo cáo tài chính riêng năm 2014",
    "Báo Cáo Tình Hình Quản Trị 6 Tháng Đầu Năm 2026",
    "Báo Cáo Tình Hình Quản Trị Năm 2025",
    "Báo cáo quản trị 6 tháng đầu năm 2014",
    "Báo Cáo Thường Niên Năm 2025",
    "Báo Cáo Phát Triển Bền Vững Năm 2025",
    "MWG | Tài Liệu Họp NĐT Quý 3/2026",
    "Định Hướng Kinh Doanh 2026",
    "Báo cáo tài chính riêng soát xét QIII năm 2017",
    "Báo cáo tài chính riêng (đã soát xét) quý III năm 2016",
    "Báo Cáo Tình Hình Quản Trị Công Ty Năm 2022",
]


@pytest.mark.parametrize("title, doc_type, period, level", REAL_TITLES)
def test_tieu_de_that(title, doc_type, period, level):
    assert classify(title) == doc_type
    assert parse_period(title, doc_type) == period
    assert assurance_level(title, doc_type) == level


@pytest.mark.parametrize("title", REAL_SKIPPED)
def test_tieu_de_that_khong_lay(title):
    assert classify(title) is None


def test_quyet_khong_khop_quy():
    # tiêu đề giả định: "quyết" không được nhận là "quý"
    assert classify("Nghị quyết Hội đồng quản trị về báo cáo hợp nhất") is None


def test_classify_quy_tac_rong_bao_loi():
    with pytest.raises(ValueError):
        classify("bat ky", rules=[{"doc_type": "X"}])


@pytest.mark.parametrize("title, doc_type, expected", [
    ("BCTC hợp nhất năm 2026", "FS_CONSOLIDATED_Q", None),  # thiếu số quý: không đoán
    ("Kết quả kinh doanh năm 2026", "MONTHLY_RESULT", None),
    ("Quý 2 năm 2026", None, "2026-Q2"),
    ("BCTC 6 tháng đầu năm 2026", None, "2026-H1"),
    ("Báo cáo thường niên 2025", None, "2025"),
    ("Báo cáo không có năm", None, None),
])
def test_parse_period_truong_hop_bien(title, doc_type, expected):
    assert parse_period(title, doc_type) == expected


def test_sources_yaml_da_dien_du():
    assert unfilled_fields(load_config()) == []


def test_unfilled_fields():
    assert unfilled_fields({"listing": {"api_base": "<...>", "lang_id": "vi-VN"}}) == ["listing.api_base"]
    assert unfilled_fields({}) == ["listing"]
