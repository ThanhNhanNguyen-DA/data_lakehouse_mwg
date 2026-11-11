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
    cfg = {"listing": {"api_base": "<...>", "languages": [{"lang_id": "vi-VN", "referer": "<...>"}]}}
    assert unfilled_fields(cfg) == ["listing.api_base", "listing.languages[0].referer"]
    assert unfilled_fields({"listing": {"api_base": "x"}}) == ["listing.languages"]
    assert unfilled_fields({}) == ["listing"]


# ---------- en-US: toàn bộ tiêu đề thật trong nhóm "ANNUAL, CORPORATE GOVERNANCE & FINANCIAL
# REPORTS" (2016-2026, khảo sát 11/11/2026). Năm 2014-2015 bản en-US không có tài liệu. ----------

EN_TAKEN = [
    # (tiêu đề, doc_type, kỳ, mức tin cậy). FY = 3, 1H = 2; "Audited" trên BCTC quý chỉ tính 2.
    ("Consolidated FS Q3/2026", "FS_CONSOLIDATED_Q", "2026-Q3", 1),
    ("Audited Consolidated FS 1H2026", "FS_CONSOLIDATED_H", "2026-H1", 2),
    ("Consolidated FS Q2/2026", "FS_CONSOLIDATED_Q", "2026-Q2", 1),
    ("Consolidated FS Q1/2026", "FS_CONSOLIDATED_Q", "2026-Q1", 1),
    ("Audited Consolidated FS FY2025", "FS_CONSOLIDATED_Y", "2025", 3),
    ("Consolidated FS Q4/2025", "FS_CONSOLIDATED_Q", "2025-Q4", 1),
    ("Consolidated FS Q3/2025", "FS_CONSOLIDATED_Q", "2025-Q3", 1),
    ("Audited Consolidated FS 1H2025", "FS_CONSOLIDATED_H", "2025-H1", 2),
    ("Consolidated FS Q2/2025", "FS_CONSOLIDATED_Q", "2025-Q2", 1),
    ("Consolidated FS Q1/2025", "FS_CONSOLIDATED_Q", "2025-Q1", 1),
    ("Audited Consolidated FS FY2024", "FS_CONSOLIDATED_Y", "2024", 3),
    ("Consolidated FS Q4/2024", "FS_CONSOLIDATED_Q", "2024-Q4", 1),
    ("Consolidated FS Q3/2024", "FS_CONSOLIDATED_Q", "2024-Q3", 1),
    ("Audited Consolidated FS 1H2024", "FS_CONSOLIDATED_H", "2024-H1", 2),
    ("Consolidated FS Q2/2024", "FS_CONSOLIDATED_Q", "2024-Q2", 1),
    ("Consolidated FS Q1/2024", "FS_CONSOLIDATED_Q", "2024-Q1", 1),
    ("Audited Consolidated FS FY2023", "FS_CONSOLIDATED_Y", "2023", 3),
    ("Consolidated FS Q4/2023", "FS_CONSOLIDATED_Q", "2023-Q4", 1),
    ("Consolidated FS Q3/2023", "FS_CONSOLIDATED_Q", "2023-Q3", 1),
    ("Audited Consolidated FS 1H2023", "FS_CONSOLIDATED_H", "2023-H1", 2),
    ("Consolidated FS Q2/2023", "FS_CONSOLIDATED_Q", "2023-Q2", 1),
    ("Consolidated FS 1Q2023", "FS_CONSOLIDATED_Q", "2023-Q1", 1),
    ("Audited Consolidated FS FY2022", "FS_CONSOLIDATED_Y", "2022", 3),
    ("Audited Consolidated FS 3Q2022", "FS_CONSOLIDATED_Q", "2022-Q3", 2),
    ("Audited Consolidated FS 1H2022", "FS_CONSOLIDATED_H", "2022-H1", 2),
    ("Consolidated Financial Statements Q2 2022", "FS_CONSOLIDATED_Q", "2022-Q2", 1),
    ("Audited Consolidated FS 1Q2022", "FS_CONSOLIDATED_Q", "2022-Q1", 2),
    ("Audited Consolidated FS FY2021", "FS_CONSOLIDATED_Y", "2021", 3),
    ("Audited Consolidated FS 3Q2021", "FS_CONSOLIDATED_Q", "2021-Q3", 2),
    ("Audited Consolidated FS 1H2021", "FS_CONSOLIDATED_H", "2021-H1", 2),
    ("Audited Consolidated FS 1Q2021", "FS_CONSOLIDATED_Q", "2021-Q1", 2),
    ("Audited Consolidated FS FY2020", "FS_CONSOLIDATED_Y", "2020", 3),
    ("Audited Consolidated FS 3Q2020", "FS_CONSOLIDATED_Q", "2020-Q3", 2),
    ("Audited Consolidated FS 1H2020", "FS_CONSOLIDATED_H", "2020-H1", 2),
    ("Audited Consolidated FS 1Q2020", "FS_CONSOLIDATED_Q", "2020-Q1", 2),
    ("Audited Consolidate FS FY2019", "FS_CONSOLIDATED_Y", "2019", 3),
    ("Interim Consolidated FS 3Q19", "FS_CONSOLIDATED_Q", "2019-Q3", 1),
    ("Interim Consolidated FS 1H19", "FS_CONSOLIDATED_H", "2019-H1", 2),
    ("Interim Consolidated FS 1Q19", "FS_CONSOLIDATED_Q", "2019-Q1", 1),
    ("Audited Consolidate FS FY2018", "FS_CONSOLIDATED_Y", "2018", 3),
    ("Interim Consolidate FS 3Q18", "FS_CONSOLIDATED_Q", "2018-Q3", 1),
    ("Interim Consolidate FS 1H18", "FS_CONSOLIDATED_H", "2018-H1", 2),
    ("Interim Consolidate FS 1Q18", "FS_CONSOLIDATED_Q", "2018-Q1", 1),
    ("Audited Consolidate FY 2017", "FS_CONSOLIDATED_Y", "2017", 3),
    ("Interim Audited Consolidate FS 3Q17", "FS_CONSOLIDATED_Q", "2017-Q3", 2),
    ("Interim Audited Consolidate FS 1H17", "FS_CONSOLIDATED_H", "2017-H1", 2),
    ("Interim Audited Consolidate FS 1Q17", "FS_CONSOLIDATED_Q", "2017-Q1", 2),
    ("Consolidate FS 2016", "FS_CONSOLIDATED_Y", "2016", 3),
    ("FS Consolidate QIII_2016", "FS_CONSOLIDATED_Q", "2016-Q3", 1),
    ("FS Consolidate 1st half_2016", "FS_CONSOLIDATED_H", "2016-H1", 2),
    ("FS Consolidate Q1_2016", "FS_CONSOLIDATED_Q", "2016-Q1", 1),
]

EN_SKIPPED = [
    "Separate FS Q3/2026", "Audited Separate FS 1H2026", "Separate FS Q2/2026",
    "Report On Corporate Governance In 6M2026", "Separate FS Q1/2026",
    "Annual Report 2025", "Sustainability Report 2025", "Audited Separate FS FY2025",
    "Report On Corporate Governance In 2025", "Separate FS Q4/2025", "Separate FS Q3/2025",
    "Audited Separate FS 1H2025", "Report On Corporate Governance In 6M2025",
    "Separate FS Q2/2025", "Separate FS Q1/2025",
    "Annual Report 2024", "Sustainability Report 2024", "Audited Separate FS FY2024",
    "Report On Corporate Governance In 2024", "Separate FS Q4/2024", "Separate FS Q3/2024",
    "Audited Separate FS 1H2024", "Separate FS Q2/2024", "Report On Corporate Governance In 6M2024",
    "Separate FS Q1/2024",
    "Sustainability Report 2023", "Annual Report 2023", "Audited Separate FS FY2023",
    "Separate FS Q4/2023", "Report On Corporate Governance In 2023", "Separate FS Q3/2023",
    "Audited Separate FS 1H2023", "Separate FS Q2/2023", "Report On Corporate Governance In 6M2023",
    "Separate FS Q1/2023",
    "Annual Report 2022", "Sustainability Report 2022", "Audited Separate FS FY2022",
    "Report On Corporate Governance 2022", "Audited Separate FS 3Q2022", "Audited Separate FS 1H2022",
    "Separate Financial Statements Q2 2022", "Report On Corporate Governance In 6M2022",
    "Audited Separate FS 1Q2022",
    "Annual Report 2021", "Audited Separate FS FY2021", "Report On Corporate Governance 2021",
    "Audited Separate FS 3Q2021", "Audited Separate FS 1H2021",
    "Report On Corporate Governance In 6M2021", "Audited Separate FS 1Q2021",
    "Annual Report 2020", "Audited Separate FS FY2020", "Report On Corporate Governance in 2020",
    "Audited Separate FS 3Q2020", "Audited Separate FS 1H2020",
    "Report On Corporate Governance in 6M2020", "Audited Separate FS 1Q2020",
    "Annual Report 2019", "Audited Separate FS FY2019", "Interim Separated FS 3Q19",
    "Interim Separated FS 1H19", "Interim Separated FS 1Q19",
    "Annual Report 2018", "Audited Separate FS FY2018", "Interim Separate FS 3Q18",
    "Interim Separate FS 1H18", "Interim Separate FS 1Q18",
    "Audited Separate FY 2017", "Interim Audited Separate FS 3Q17", "Interim Audited Separate FS 1H17",
    "Interim Audited Separate FS 1Q17",
    "Annual Report 2016", "Separate FS 2016", "FS Separate QIII_2016", "FS Separate 1st half_2016",
    "FS Separate Q1_2016",
]


@pytest.mark.parametrize("title, doc_type, period, level", EN_TAKEN)
def test_tieu_de_en_that(title, doc_type, period, level):
    assert classify(title) == doc_type
    assert parse_period(title, doc_type) == period
    assert assurance_level(title, doc_type, lang="en-US") == level


@pytest.mark.parametrize("title", EN_SKIPPED)
def test_tieu_de_en_that_khong_lay(title):
    assert classify(title) is None


@pytest.mark.parametrize("title", ["Parent Company Consolidated FS Q1/2026",  # giả định
                                   "Consolidated FS of the Parent 2016"])
def test_en_loai_tru_parent(title):
    assert classify(title) is None


def test_tu_khoa_tien_to():
    rules = [{"doc_type": "X", "all": ["consolidat*"], "any": ["3q*"]}]
    assert classify("Consolidated FS 3Q19", rules) == "X"
    assert classify("Consolidate 3Q2020", rules) == "X"
    assert classify("Unconsolidated FS 3Q19", rules) is None  # tiền tố phải ở đầu từ
