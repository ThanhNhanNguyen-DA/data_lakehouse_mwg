"""Bóc tách thật (OCR) trên 2 PDF mẫu: trang B01/B02/B03 của BCTC năm 2025 (vi) và 2018 (en).

Cần Tesseract + gói vie/eng (xem README); không có thì bỏ qua.
Số kỳ vọng nằm trong tests/fixtures/expected_values.csv. Cột amount_vnd để trống cho người dùng
tự đọc từ PDF và điền; dòng còn trống không được kiểm tra. Không điền số từ trí nhớ.
"""
import csv
import hashlib
import os
import shutil
from datetime import date
from functools import lru_cache
from pathlib import Path

import pytest

from src.common.paths import ROOT
from src.extraction.pdf_extractor import check_equations, load_dim_account, load_equations, signs_of

FIXTURES = ROOT / "tests" / "fixtures"
CASES = {  # vị trí trang báo cáo trong file mẫu (đã xem bằng mắt khi cắt trang)
    "vi_2025_FY_statements.pdf": ({"B01": [1, 2], "B02": [3], "B03": [4, 5]}, date(2025, 12, 31)),
    "en_2018_FY_statements.pdf": ({"B01": [1, 2], "B02": [3], "B03": [4, 5]}, date(2018, 12, 31)),
}

pytestmark = pytest.mark.skipif(not (shutil.which("tesseract") or os.environ.get("TESSERACT_CMD")),
                                reason="chưa cài Tesseract (xem README)")


@lru_cache(maxsize=None)
def _extract(name: str) -> dict:
    from src.extraction.run_extraction import _dedupe, extract_fs

    path = FIXTURES / name
    row = {"path": path.relative_to(ROOT).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
           "has_text_layer": False}
    res = extract_fs(row)
    assert res["error"] is None, res["error"]
    res["cells"] = _dedupe(res["cells"])
    return res


@pytest.mark.parametrize("name", CASES)
def test_tim_dung_trang_bao_cao(name):
    assert _extract(name)["pages"] == CASES[name][0]


@pytest.mark.parametrize("name", CASES)
def test_ngay_bao_cao(name):
    assert _extract(name)["end"] == CASES[name][1]


@pytest.mark.parametrize("name", CASES)
def test_regime_tt200(name):
    assert _extract(name)["regime"] == "TT200"


@pytest.mark.parametrize("name", CASES)
def test_phuong_trinh_ke_toan(name):
    """Phương trình của regime. Chỉ kiểm phương trình có vế trừ khi dim_account đã khai báo sign:
    BCTC TT200 của MWG in khoản giảm trừ là số âm, chưa có sign thì không đánh giá đúng được."""
    from src.extraction.run_extraction import DIM_ACCOUNT, EQUATIONS

    res = _extract(name)
    values = {(c["statement"], c["account_code"], c["period_kind"]): c["amount_vnd"] for c in res["cells"]}
    signs = signs_of(load_dim_account(DIM_ACCOUNT), res["regime"])
    eqs = [e for e in load_equations(EQUATIONS)
           if all(op > 0 or (e["statement"], code) in signs for op, code in e["terms"])]
    assert eqs, "không có phương trình nào kiểm được"
    failed = [c for c in check_equations(values, eqs, res["regime"], signs) if not c["ok"]]
    assert not failed, failed


def _expected(name: str) -> list[dict]:
    with (FIXTURES / "expected_values.csv").open(encoding="utf-8", newline="") as f:
        return [r for r in csv.DictReader(f) if r["fixture"] == name and r["amount_vnd"].strip()]


@pytest.mark.parametrize("name", CASES)
def test_so_ky_vong_doc_tu_pdf(name):
    expected = _expected(name)
    if not expected:
        pytest.skip("chưa điền số kỳ vọng trong tests/fixtures/expected_values.csv")
    got = {(c["statement"], c["account_code"], c["period_kind"]): c["amount_vnd"] for c in _extract(name)["cells"]}
    wrong = [(r["statement"], r["account_code"], r["period_kind"], r["amount_vnd"],
              got.get((r["statement"], r["account_code"], r["period_kind"])))
             for r in expected
             if got.get((r["statement"], r["account_code"], r["period_kind"])) != int(r["amount_vnd"].replace(".", "").replace(",", ""))]
    assert not wrong, wrong
