"""Tiêu đề trong test là ví dụ giả định theo cách đặt tên BCTC phổ biến ở Việt Nam.
Sau khi khảo sát mwg.vn, bổ sung tiêu đề thật vào đây để khóa hành vi của doc_rules."""
import pytest

from src.ingestion.mwg_crawler import classify, load_config, parse_period, unfilled_fields


@pytest.mark.parametrize("title, expected", [
    ("Báo cáo tài chính hợp nhất Quý 3 năm 2026", "FS_CONSOLIDATED_Q"),
    ("BCTC hợp nhất quý 3/2026", "FS_CONSOLIDATED_Q"),
    ("Báo cáo tài chính hợp nhất giữa niên độ đã soát xét 2026", "FS_CONSOLIDATED_H"),
    ("Báo cáo tài chính hợp nhất năm 2025 đã kiểm toán", "FS_CONSOLIDATED_Y"),
    ("Kết quả kinh doanh lũy kế 10 tháng 2026", "MONTHLY_RESULT"),
    ("Audited consolidated financial statements 2025", "FS_CONSOLIDATED_Y"),
    ("Consolidated financial statements Q3 2026", "FS_CONSOLIDATED_Q"),
    ("Nghị quyết Hội đồng quản trị về báo cáo hợp nhất", None),  # "quyet" không khớp "quy"
    ("Báo cáo tài chính riêng Quý 3 năm 2026", None),
    ("Thông báo ngày đăng ký cuối cùng", None),
])
def test_classify(title, expected):
    assert classify(title) == expected


def test_classify_quy_tac_rong_bao_loi():
    with pytest.raises(ValueError):
        classify("bat ky", rules=[{"doc_type": "X"}])


@pytest.mark.parametrize("title, doc_type, expected", [
    ("Báo cáo tài chính hợp nhất Quý 3 năm 2026", "FS_CONSOLIDATED_Q", "2026-Q3"),
    ("BCTC hợp nhất quý 3/2026", "FS_CONSOLIDATED_Q", "2026-Q3"),
    ("BCTC hợp nhất Quý IV năm 2025", "FS_CONSOLIDATED_Q", "2025-Q4"),
    ("Consolidated financial statements Q1 2026", "FS_CONSOLIDATED_Q", "2026-Q1"),
    ("BCTC hợp nhất năm 2026", "FS_CONSOLIDATED_Q", None),  # thiếu số quý: không đoán
    ("Báo cáo tài chính hợp nhất giữa niên độ đã soát xét 2026", "FS_CONSOLIDATED_H", "2026-H1"),
    ("Báo cáo tài chính hợp nhất năm 2025 đã kiểm toán", "FS_CONSOLIDATED_Y", "2025"),
    ("Kết quả kinh doanh lũy kế 10 tháng 2026", "MONTHLY_RESULT", "2026-M10"),
    ("Kết quả kinh doanh tháng 9/2026", "MONTHLY_RESULT", "2026-M09"),
    ("Kết quả kinh doanh năm 2026", "MONTHLY_RESULT", None),
    ("Quý 2 năm 2026", None, "2026-Q2"),
    ("BCTC 6 tháng đầu năm 2026", None, "2026-H1"),
    ("Báo cáo thường niên 2025", None, "2025"),
    ("Báo cáo không có năm", None, None),
])
def test_parse_period(title, doc_type, expected):
    assert parse_period(title, doc_type) == expected


def test_sources_yaml_bao_cac_truong_chua_dien():
    missing = unfilled_fields(load_config())
    # link_selector đã có giá trị mặc định, các trường còn lại phải được khảo sát
    assert "listing_pages[0].link_selector" not in missing


def test_unfilled_fields():
    cfg = {"listing_pages": [{"url": "https://example.com/{page}", "item_selector": "<...>"}]}
    assert unfilled_fields(cfg) == ["listing_pages[0].item_selector"]
    assert unfilled_fields({}) == ["listing_pages"]
