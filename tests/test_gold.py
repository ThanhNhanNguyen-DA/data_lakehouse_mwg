"""Test Gold: chỉ ô gold_eligible, grain không trùng, scope tách bạch, view KPI (NULLIF) và độ phủ.

Số tiền giả lập (không phải số liệu MWG)."""
from datetime import date

import duckdb
import polars as pl
import pyarrow as pa
import pytest
from deltalake import write_deltalake

from src.transform import silver_to_gold as G
from tests.test_silver import frame, row


def _silver() -> pl.DataFrame:
    q = dict(statement="B02", period_kind="quarter", fiscal_year=2025)
    b = dict(statement="B01", period_kind="instant", fiscal_year=2025)
    rows = [row(**q, fiscal_quarter=2, account_code="10", amount_vnd=1000, period_end=date(2025, 6, 30)),
            row(**q, fiscal_quarter=2, account_code="20", amount_vnd=200, period_end=date(2025, 6, 30)),
            row(**q, fiscal_quarter=2, account_code="60", amount_vnd=50, period_end=date(2025, 6, 30)),
            row(**q, fiscal_quarter=3, account_code="10", amount_vnd=0, period_end=date(2025, 9, 30)),     # NULLIF
            row(**q, fiscal_quarter=3, account_code="20", amount_vnd=10, period_end=date(2025, 9, 30)),
            row(**q, fiscal_quarter=1, account_code="10", amount_vnd=800, period_end=date(2025, 3, 31),
                gold_eligible=False),                                                                    # bị chặn DQ
            row(**b, fiscal_quarter=1, account_code="410", amount_vnd=900, period_end=date(2025, 3, 31)),
            row(**b, fiscal_quarter=2, account_code="410", amount_vnd=1100, period_end=date(2025, 6, 30)),
            row(**b, fiscal_quarter=2, account_code="270", amount_vnd=4000, period_end=date(2025, 6, 30)),
            row(statement="B03", period_kind="ytd", fiscal_quarter=2, account_code="20", amount_vnd=5)]
    return frame(rows)


@pytest.fixture
def gold(tmp_path):
    silver = _silver()
    write_deltalake(str(tmp_path / "silver"), silver.to_arrow())
    pl.DataFrame({"sha256": ["a" * 64], "title": ["BCTC"], "path": ["data/bronze/x.pdf"], "url": ["u"],
                  "published": ["2025-07-30"], "assurance_level": [2]}).write_parquet(tmp_path / "manifest.parquet")
    res = G.write_gold(silver, tmp_path / "gold")
    G.build_duckdb(tmp_path / "gold", tmp_path / "silver", tmp_path / "manifest.parquet", tmp_path / "mwg.duckdb",
                   root=tmp_path)
    return tmp_path, res


def _q(tmp_path, sql):
    import os
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        con = duckdb.connect(str(tmp_path / "mwg.duckdb"), read_only=True)
        con.sql("INSTALL delta; LOAD delta")
        return con.sql(sql).fetchall()
    finally:
        os.chdir(cwd)


def test_fact_chi_nhan_o_gold_eligible_va_mot_scope(gold):
    tmp_path, res = gold
    fact = pl.read_delta(str(tmp_path / "gold" / "fact_financial"))
    assert res["fact"] == 8 == fact.height  # bỏ ô bị chặn và B03
    assert set(fact["scope"]) == {"consolidated"} and set(fact["chain_key"]) == {"MWG_TOTAL"}
    assert fact.group_by(G.FACT_KEY).len()["len"].max() == 1


def test_fact_trung_khoa_thi_khong_ghi(tmp_path):
    s = _silver()
    with pytest.raises(ValueError, match="trùng khóa"):
        G.write_gold(pl.concat([s, s]), tmp_path / "gold")


def test_rang_buoc_scope_tach_bach(gold):
    tmp_path, _ = gold
    fact = pl.read_delta(str(tmp_path / "gold" / "fact_financial"))
    bad = fact.head(1).with_columns(pl.lit("segment").alias("scope"))  # segment mà chain_key = MWG_TOTAL
    with pytest.raises(Exception):
        write_deltalake(str(tmp_path / "gold" / "fact_financial"), bad.to_arrow(), mode="append",
                        partition_by=["fiscal_year"])


def test_kpi_quarter_va_nullif(gold):
    tmp_path, _ = gold
    rows = dict((r[0], r[1:]) for r in _q(tmp_path, "SELECT fiscal_quarter, gross_margin, net_margin, roe_annualized, "
                                                    "net_revenue FROM v_kpi_quarter WHERE fiscal_year = 2025"))
    gm, nm, roe, _ = rows[2]
    assert gm == pytest.approx(0.2) and nm == pytest.approx(0.05)
    assert roe == pytest.approx(4 * 50 / 1000)  # bình quân 410 (900 + 1100) / 2
    assert rows[3][0] is None and rows[3][3] == 0  # doanh thu 0 -> biên NULL, không lỗi chia 0
    assert 1 not in rows  # quý 1 bị chặn DQ: không có trong KPI


def test_coverage_ba_trang_thai(gold):
    tmp_path, _ = gold
    st = dict(_q(tmp_path, "SELECT fiscal_quarter, status FROM v_coverage WHERE fiscal_year = 2025 "
                           "AND account_code = '10' AND period_kind = 'quarter'"))
    assert st == {1: "bị chặn DQ", 2: "có số", 3: "có số", 4: "không có tài liệu gốc"}


def test_lineage_noi_ve_tai_lieu(gold):
    tmp_path, _ = gold
    (path, page, origin), = _q(tmp_path, "SELECT source_path, page, value_origin FROM v_lineage "
                                         "WHERE account_code = '10' AND fiscal_quarter = 2")
    assert path == "data/bronze/x.pdf" and page == 9 and origin == "ocr"


def test_kpi_thieu_dau_ky_tinh_theo_so_du_cuoi_ky(gold):
    tmp_path, _ = gold
    rows = dict((r[0], r[1:]) for r in _q(tmp_path, "SELECT fiscal_quarter, roe_annualized, kpi_method FROM v_kpi_quarter"))
    # Q2: có 410 đầu kỳ (Q1) và cuối kỳ; thiếu 270/140 đầu kỳ -> ending_balance, ROE vẫn theo bình quân 410
    assert rows[2] == (pytest.approx(4 * 50 / 1000), "ending_balance")


def test_snapshot_tu_chua_khong_can_delta(gold, tmp_path_factory):
    tmp_path, _ = gold
    out = tmp_path_factory.mktemp("snap") / "mwg.duckdb"
    assert G.export_snapshot(tmp_path / "gold", tmp_path / "silver", tmp_path / "manifest.parquet", out) > 0
    con = duckdb.connect(str(out), read_only=True)  # không LOAD delta, thư mục làm việc bất kỳ
    kinds = dict(con.sql("SELECT table_name, table_type FROM information_schema.tables").fetchall())
    assert kinds["fact_financial"] == "BASE TABLE" and kinds["v_kpi_quarter"] == "VIEW"
    assert con.sql("SELECT count(*) FROM fact_financial").fetchall()[0][0] == 8
    assert con.sql("SELECT gross_margin FROM v_kpi_quarter WHERE fiscal_quarter = 2").fetchall()[0][0] == pytest.approx(0.2)
