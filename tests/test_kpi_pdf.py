"""KPI trong Gold khớp số tự tính trên PDF gốc (Definition of Done, CLAUDE.md mục 7).

Số trong tests/fixtures/kpi_pdf_values.csv được đọc trực tiếp trên ảnh trang PDF trong Bronze (sha256 + trang ghi
trong file), không lấy từ Gold hay nguồn khác. Bỏ qua khi chưa dựng Gold (data/gold/mwg.duckdb).
"""
import csv
import os
from pathlib import Path

import pytest

from src.common.paths import GOLD_DIR, ROOT

DB = GOLD_DIR / "mwg.duckdb"
pytestmark = pytest.mark.skipif(not DB.exists(), reason="chưa dựng Gold")


def _pdf() -> dict:
    with (Path(__file__).parent / "fixtures" / "kpi_pdf_values.csv").open(encoding="utf-8") as f:
        return {(r["period"], r["account_code"], r["column"]): int(r["amount_vnd"]) for r in csv.DictReader(f)}


def _kpi(sql: str) -> tuple:
    import duckdb
    cwd = os.getcwd()
    os.chdir(ROOT)
    try:
        con = duckdb.connect(str(DB), read_only=True)
        con.sql("INSTALL delta; LOAD delta")
        return con.execute(sql).fetchone()
    finally:
        os.chdir(cwd)


def test_kpi_nam_2024_khop_pdf():
    v = _pdf()
    gm, nm, roe = _kpi("SELECT gross_margin, net_margin, roe FROM v_kpi_year WHERE fiscal_year = 2024")
    rev = v[("FY2024", "10", "current")]
    assert gm == pytest.approx(v[("FY2024", "20", "current")] / rev, abs=1e-9)
    assert nm == pytest.approx(v[("FY2024", "60", "current")] / rev, abs=1e-9)
    eq = (v[("FY2024", "410", "closing")] + v[("FY2024", "410", "opening")]) / 2
    assert roe == pytest.approx(v[("FY2024", "60", "current")] / eq, abs=1e-9)


def test_kpi_quy_3_2025_khop_pdf():
    v = _pdf()
    gm, nm, yoy = _kpi("SELECT gross_margin, net_margin, revenue_yoy FROM v_kpi_quarter "
                       "WHERE fiscal_year = 2025 AND fiscal_quarter = 3")
    rev = v[("Q3/2025", "10", "q_current")]
    assert gm == pytest.approx(v[("Q3/2025", "20", "q_current")] / rev, abs=1e-9)
    assert nm == pytest.approx(v[("Q3/2025", "60", "q_current")] / rev, abs=1e-9)
    assert yoy == pytest.approx(rev / v[("Q3/2025", "10", "q_prior")] - 1, abs=1e-9)
