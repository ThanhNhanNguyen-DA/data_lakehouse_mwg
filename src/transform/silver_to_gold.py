"""silver/fin_statement_long -> Gold: dim_date, dim_account, dim_chain, fact_financial (Delta) + data/gold/mwg.duckdb.

    python -m src.transform.silver_to_gold

fact_financial: grain = 1 chỉ tiêu × 1 kỳ (fiscal_year, fiscal_quarter, period_kind) × 1 phạm vi (chain_key, scope).
Chỉ nhận ô gold_eligible của Silver (và ô suy ra từ hai ô gold_eligible). Số hợp nhất: chain_key = MWG_TOTAL,
scope = consolidated; số theo chuỗi (chưa có, chain_kpi để sau) sẽ dùng scope = segment. Không cộng lẫn hai scope:
ràng buộc CHECK trên bảng Delta và mọi view KPI lọc scope = 'consolidated'.

mwg.duckdb chỉ chứa view (delta_scan theo đường dẫn tương đối từ gốc repo): mở DuckDB với thư mục làm việc = gốc repo.
"""
from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path

import duckdb
import polars as pl
from deltalake import DeltaTable, write_deltalake

from src.common.paths import CONFIG_DIR, GOLD_DIR, ROOT, SILVER_DIR

SILVER_TABLE = SILVER_DIR / "fin_statement_long"
DUCKDB_PATH = GOLD_DIR / "mwg.duckdb"
# Bản chụp gọn cho Streamlit Community Cloud: bảng vật chất hóa + view KPI trong một file DuckDB, không cần extension
# delta và không chứa PDF. Dashboard đọc file này khi không có data/gold (repo không commit data/gold).
SNAPSHOT_PATH = ROOT / "dashboard" / "snapshot" / "mwg.duckdb"
MANIFEST_COLS = ["sha256", "title", "path", "url", "published", "assurance_level", "lang", "doc_type", "period"]
CONSOLIDATED = ("MWG_TOTAL", "consolidated")
FACT_CONSTRAINTS = {
    "scope_ok": "scope IN ('consolidated', 'segment')",
    "scope_chain_ok": "(scope = 'consolidated' AND chain_key = 'MWG_TOTAL') OR (scope = 'segment' AND chain_key <> 'MWG_TOTAL')",
    "amount_not_null": "amount_vnd IS NOT NULL",
}
FACT_KEY = ["fiscal_year", "fiscal_quarter", "period_kind", "statement", "account_code", "chain_key", "scope"]


def build_fact(silver: pl.DataFrame) -> pl.DataFrame:
    """Ô gold_eligible của Silver -> fact hợp nhất (B01, B02, B03; B03 qua critical riêng theo từng cột)."""
    df = silver.filter(pl.col("gold_eligible") & pl.col("statement").is_in(["B01", "B02", "B03"]))
    return df.select(
        "fiscal_year", "fiscal_quarter", "period_kind", "period_end",
        (pl.col("period_end").dt.year() * 10000 + pl.col("period_end").dt.month() * 100
         + pl.col("period_end").dt.day()).cast(pl.Int32).alias("date_key"),
        "statement", "account_code", pl.lit(CONSOLIDATED[0]).alias("chain_key"), pl.lit(CONSOLIDATED[1]).alias("scope"),
        "amount_vnd", "regime", "semantic_break", "is_derived", "is_comparative", "is_manual", "source_doc_sha256",
        "source_period", "source_lang", "source_priority", "page", "value_origin", "derived_from", "gold_basis")


def build_dim_date(fact: pl.DataFrame) -> pl.DataFrame:
    """Mọi ngày cuối quý từ năm nhỏ nhất tới lớn nhất của fact (kể cả quý không có số, để thấy khoảng trống)."""
    years = range(fact["fiscal_year"].min(), fact["fiscal_year"].max() + 1) if fact.height else range(0)
    rows = []
    for y in years:
        for q in (1, 2, 3, 4):
            end = date(y, 3 * q, 31 if q in (1, 4) else 30)
            rows.append({"date_key": y * 10000 + end.month * 100 + end.day, "period_end": end, "fiscal_year": y,
                         "fiscal_quarter": q, "quarter_label": f"Q{q}/{y}", "is_year_end": q == 4})
    return pl.DataFrame(rows, schema_overrides={"date_key": pl.Int32, "fiscal_year": pl.Int32, "fiscal_quarter": pl.Int32})


def build_dim_account() -> pl.DataFrame:
    """Mã chuẩn (TT200) từ config/dim_account.csv: Gold dùng mã chuẩn; mã TT99 đã quy đổi ở Silver."""
    with (CONFIG_DIR / "dim_account.csv").open(encoding="utf-8", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["regime"] == "TT200"]
    df = pl.DataFrame(rows).select("statement", "account_code", "name_vi", "name_en", "level", "parent_code",
                                   "is_subtotal", "is_additive", "sign")
    return df.with_columns(pl.col("level").cast(pl.Int32), pl.col("sign").cast(pl.Int32, strict=False))


def build_dim_chain() -> pl.DataFrame:
    return pl.read_csv(CONFIG_DIR / "dim_chain.csv", infer_schema=False)


def write_gold(silver: pl.DataFrame, gold_dir: Path = GOLD_DIR) -> dict:
    fact = build_fact(silver)
    dup = fact.group_by(FACT_KEY).len().filter(pl.col("len") > 1)
    if dup.height:  # grain bị vỡ: không ghi Gold
        raise ValueError(f"[gold] fact_financial trùng khóa {dup.height} nhóm: {dup.head(3).to_dicts()}")
    gold_dir.mkdir(parents=True, exist_ok=True)
    for name, df in (("dim_date", build_dim_date(fact)), ("dim_account", build_dim_account()),
                     ("dim_chain", build_dim_chain())):
        df.write_delta(str(gold_dir / name), mode="overwrite", delta_write_options={"schema_mode": "overwrite"})
    path = str(gold_dir / "fact_financial")
    # Gold dựng lại toàn bộ: thêm/bớt cột (vd. gold_basis) thì ghi đè cả schema
    write_deltalake(path, fact.to_arrow(), mode="overwrite", partition_by=["fiscal_year"], schema_mode="overwrite")
    dt = DeltaTable(path)
    have = dt.metadata().configuration
    missing = {k: v for k, v in FACT_CONSTRAINTS.items() if f"delta.constraints.{k}" not in have}
    if missing:
        dt.alter.add_constraint(missing)
    # Gold dựng lại toàn bộ mỗi lần và được commit lên git cho Streamlit Cloud: chỉ giữ file của phiên bản hiện tại
    # (time travel để ở Silver). Delta log vẫn ghi đủ lịch sử thao tác.
    for name in ("dim_date", "dim_account", "dim_chain", "fact_financial"):
        DeltaTable(str(gold_dir / name)).vacuum(retention_hours=0, enforce_retention_duration=False, dry_run=False)
    return {"fact": fact.height, "version": DeltaTable(path).version()}


# ---------- DuckDB: view trên bảng Delta ----------

KPI_SQL = """
-- B01 tại từng ngày cuối quý (số hợp nhất)
CREATE OR REPLACE VIEW v_balance AS
SELECT fiscal_year, fiscal_quarter, period_end,
       max(regime) AS regime,
       bool_or(semantic_break) AS has_semantic_break,
       max(amount_vnd) FILTER (account_code = '100') AS current_assets,
       max(amount_vnd) FILTER (account_code = '110') AS cash,
       max(amount_vnd) FILTER (account_code = '120') AS st_investments,
       max(amount_vnd) FILTER (account_code = '130') AS st_receivables,
       max(amount_vnd) FILTER (account_code = '140') AS inventory,
       max(amount_vnd) FILTER (account_code = '150') AS other_current_assets,
       max(amount_vnd) FILTER (account_code = '200') AS non_current_assets,
       max(amount_vnd) FILTER (account_code = '270') AS total_assets,
       max(amount_vnd) FILTER (account_code = '300') AS liabilities,
       max(amount_vnd) FILTER (account_code = '400') AS equity,
       max(amount_vnd) FILTER (account_code = '410') AS owners_equity
FROM fact_financial
WHERE scope = 'consolidated' AND statement = 'B01' AND period_kind = 'instant'
GROUP BY ALL;

-- B02 theo kỳ (quarter / year)
CREATE OR REPLACE VIEW v_income AS
SELECT fiscal_year, fiscal_quarter, period_kind,
       max(amount_vnd) FILTER (account_code = '10') AS net_revenue,
       max(amount_vnd) FILTER (account_code = '11') AS cogs,
       max(amount_vnd) FILTER (account_code = '20') AS gross_profit,
       max(amount_vnd) FILTER (account_code = '60') AS npat,
       max(amount_vnd) FILTER (account_code = '61') AS npat_parent,
       bool_or(is_derived) AS has_derived,
       -- regime/semantic_break của tài liệu nguồn: kỳ chỉ có số so sánh (không có B01 cùng ngày) vẫn có giá trị
       max(regime) AS regime,
       bool_or(semantic_break) AS income_semantic_break
FROM fact_financial
WHERE scope = 'consolidated' AND statement = 'B02' AND period_kind IN ('quarter', 'year')
GROUP BY ALL;

CREATE OR REPLACE VIEW v_kpi_quarter AS
WITH q AS (
    SELECT i.*, d.period_end, d.quarter_label,
           -- ngày cuối quý trước (đầu kỳ) để lấy số bình quân
           CASE WHEN i.fiscal_quarter = 1 THEN i.fiscal_year - 1 ELSE i.fiscal_year END AS prev_year,
           CASE WHEN i.fiscal_quarter = 1 THEN 4 ELSE i.fiscal_quarter - 1 END AS prev_quarter
    FROM v_income i JOIN dim_date d USING (fiscal_year, fiscal_quarter)
    WHERE i.period_kind = 'quarter'
)
SELECT q.fiscal_year, q.fiscal_quarter, q.quarter_label, q.period_end, coalesce(b.regime, q.regime) AS regime,
       q.net_revenue, q.gross_profit, q.npat, q.npat_parent, q.cogs, q.has_derived,
       q.gross_profit / NULLIF(q.net_revenue, 0) AS gross_margin,
       q.npat / NULLIF(q.net_revenue, 0) AS net_margin,
       -- năm hóa ×4; bình quân đầu kỳ/cuối kỳ, thiếu số đầu kỳ thì dùng số dư cuối kỳ (kpi_method), thiếu cuối kỳ: NULL
       4 * q.npat / NULLIF(coalesce((b.owners_equity + bp.owners_equity) / 2, b.owners_equity), 0) AS roe_annualized,
       4 * q.npat / NULLIF(coalesce((b.total_assets + bp.total_assets) / 2, b.total_assets), 0) AS roa_annualized,
       coalesce((b.inventory + bp.inventory) / 2, b.inventory) / NULLIF(abs(q.cogs), 0) * 91.25 AS inventory_days,
       CASE WHEN bp.owners_equity IS NOT NULL AND bp.total_assets IS NOT NULL AND bp.inventory IS NOT NULL
                 AND b.owners_equity IS NOT NULL AND b.total_assets IS NOT NULL AND b.inventory IS NOT NULL THEN 'average'
            WHEN b.owners_equity IS NOT NULL OR b.total_assets IS NOT NULL OR b.inventory IS NOT NULL THEN 'ending_balance'
       END AS kpi_method,
       q.net_revenue / NULLIF(y.net_revenue, 0) - 1 AS revenue_yoy,
       q.npat / NULLIF(y.npat, 0) - 1 AS npat_yoy,
       q.net_revenue / NULLIF(p.net_revenue, 0) - 1 AS revenue_qoq,
       q.npat / NULLIF(p.npat, 0) - 1 AS npat_qoq,
       b.total_assets, b.current_assets / NULLIF(b.total_assets, 0) AS current_assets_share,
       b.non_current_assets / NULLIF(b.total_assets, 0) AS non_current_assets_share,
       b.cash / NULLIF(b.total_assets, 0) AS cash_share,
       b.inventory / NULLIF(b.total_assets, 0) AS inventory_share,
       b.liabilities / NULLIF(b.total_assets, 0) AS liabilities_share,
       b.equity / NULLIF(b.total_assets, 0) AS equity_share,
       coalesce(b.has_semantic_break, false) OR coalesce(q.income_semantic_break, false) AS has_semantic_break
FROM q
LEFT JOIN v_balance b ON b.fiscal_year = q.fiscal_year AND b.fiscal_quarter = q.fiscal_quarter
LEFT JOIN v_balance bp ON bp.fiscal_year = q.prev_year AND bp.fiscal_quarter = q.prev_quarter
LEFT JOIN (SELECT * FROM v_income WHERE period_kind = 'quarter') y
       ON y.fiscal_year = q.fiscal_year - 1 AND y.fiscal_quarter = q.fiscal_quarter
LEFT JOIN (SELECT * FROM v_income WHERE period_kind = 'quarter') p
       ON p.fiscal_year = q.prev_year AND p.fiscal_quarter = q.prev_quarter;

CREATE OR REPLACE VIEW v_kpi_year AS
SELECT i.fiscal_year, coalesce(b.regime, i.regime) AS regime, i.net_revenue, i.gross_profit, i.npat, i.npat_parent, i.cogs,
       i.gross_profit / NULLIF(i.net_revenue, 0) AS gross_margin,
       i.npat / NULLIF(i.net_revenue, 0) AS net_margin,
       i.npat / NULLIF(coalesce((b.owners_equity + bp.owners_equity) / 2, b.owners_equity), 0) AS roe,
       i.npat / NULLIF(coalesce((b.total_assets + bp.total_assets) / 2, b.total_assets), 0) AS roa,
       coalesce((b.inventory + bp.inventory) / 2, b.inventory) / NULLIF(abs(i.cogs), 0) * 365 AS inventory_days,
       CASE WHEN bp.owners_equity IS NOT NULL AND bp.total_assets IS NOT NULL AND bp.inventory IS NOT NULL
                 AND b.owners_equity IS NOT NULL AND b.total_assets IS NOT NULL AND b.inventory IS NOT NULL THEN 'average'
            WHEN b.owners_equity IS NOT NULL OR b.total_assets IS NOT NULL OR b.inventory IS NOT NULL THEN 'ending_balance'
       END AS kpi_method,
       i.net_revenue / NULLIF(y.net_revenue, 0) - 1 AS revenue_yoy,
       i.npat / NULLIF(y.npat, 0) - 1 AS npat_yoy,
       b.total_assets, b.current_assets / NULLIF(b.total_assets, 0) AS current_assets_share,
       b.non_current_assets / NULLIF(b.total_assets, 0) AS non_current_assets_share,
       b.cash / NULLIF(b.total_assets, 0) AS cash_share,
       b.inventory / NULLIF(b.total_assets, 0) AS inventory_share,
       b.liabilities / NULLIF(b.total_assets, 0) AS liabilities_share,
       b.equity / NULLIF(b.total_assets, 0) AS equity_share,
       coalesce(b.has_semantic_break, false) OR coalesce(i.income_semantic_break, false) AS has_semantic_break
FROM v_income i
LEFT JOIN v_balance b ON b.fiscal_year = i.fiscal_year AND b.fiscal_quarter = 4
LEFT JOIN v_balance bp ON bp.fiscal_year = i.fiscal_year - 1 AND bp.fiscal_quarter = 4
LEFT JOIN (SELECT * FROM v_income WHERE period_kind = 'year') y ON y.fiscal_year = i.fiscal_year - 1
WHERE i.period_kind = 'year';

-- Cơ cấu tài sản – nguồn vốn dạng dài; mã TT99 đổi nội dung (semantic_break) không nối chuỗi với TT200
CREATE OR REPLACE VIEW v_balance_structure AS
SELECT f.fiscal_year, f.fiscal_quarter, f.period_end, f.account_code, a.name_vi, f.amount_vnd, f.regime, f.semantic_break,
       f.amount_vnd / NULLIF(t.amount_vnd, 0) AS share_of_total_assets,
       -- chuỗi liền mạch: đổi regime ở mã semantic_break thì bắt đầu chuỗi mới
       f.account_code || CASE WHEN f.semantic_break THEN ' (TT99)' ELSE '' END AS series_key
FROM fact_financial f
JOIN fact_financial t ON t.scope = 'consolidated' AND t.statement = 'B01' AND t.account_code = '270'
     AND t.period_kind = 'instant' AND t.fiscal_year = f.fiscal_year AND t.fiscal_quarter = f.fiscal_quarter
LEFT JOIN dim_account a ON a.statement = 'B01' AND a.account_code = f.account_code
WHERE f.scope = 'consolidated' AND f.statement = 'B01' AND f.period_kind = 'instant'
  AND f.account_code IN ('110', '120', '130', '140', '150', '200', '300', '400');

-- Độ phủ: kỳ × chỉ tiêu chính -> có số / bị chặn DQ / không có tài liệu gốc
CREATE OR REPLACE VIEW v_coverage AS
WITH indicators(statement, account_code, period_kind, indicator) AS (VALUES
    ('B02', '10', 'quarter', 'Doanh thu thuần (quý)'), ('B02', '20', 'quarter', 'LN gộp (quý)'),
    ('B02', '60', 'quarter', 'LNST (quý)'), ('B02', '61', 'quarter', 'LNST cổ đông công ty mẹ (quý)'),
    ('B02', '10', 'year', 'Doanh thu thuần (năm)'), ('B02', '60', 'year', 'LNST (năm)'),
    ('B01', '270', 'instant', 'Tổng tài sản'), ('B01', '410', 'instant', 'Vốn chủ sở hữu'),
    ('B01', '140', 'instant', 'Hàng tồn kho')),
spine AS (
    SELECT d.fiscal_year, CASE WHEN i.period_kind = 'year' THEN 0 ELSE d.fiscal_quarter END AS fiscal_quarter,
           CASE WHEN i.period_kind = 'year' THEN 'FY' || d.fiscal_year ELSE d.quarter_label END AS period_label,
           d.period_end, i.*
    FROM dim_date d CROSS JOIN indicators i
    WHERE i.period_kind <> 'year' OR d.fiscal_quarter = 4)
SELECT s.fiscal_year, s.fiscal_quarter, s.period_label, s.period_end, s.statement, s.account_code, s.period_kind,
       s.indicator,
       CASE WHEN f.amount_vnd IS NOT NULL THEN 'có số'
            WHEN sv.amount_vnd IS NOT NULL THEN 'bị chặn DQ'
            ELSE 'không có tài liệu gốc' END AS status,
       f.amount_vnd, sv.amount_vnd AS silver_amount_vnd, sv.source_period AS silver_source_period
FROM spine s
LEFT JOIN fact_financial f ON f.scope = 'consolidated' AND f.statement = s.statement AND f.account_code = s.account_code
     AND f.period_kind = s.period_kind AND f.fiscal_year = s.fiscal_year AND f.fiscal_quarter = s.fiscal_quarter
LEFT JOIN silver_fin_statement_long sv ON sv.statement = s.statement AND sv.account_code = s.account_code
     AND sv.period_kind = s.period_kind AND sv.fiscal_year = s.fiscal_year AND sv.fiscal_quarter = s.fiscal_quarter;

-- Truy vết một số trong Gold về tài liệu Bronze, trang, nguồn gốc giá trị
CREATE OR REPLACE VIEW v_lineage AS
SELECT f.*, a.name_vi, d.title AS source_title, d.path AS source_path, d.url AS source_url,
       d.published AS source_published, d.assurance_level
FROM fact_financial f
LEFT JOIN dim_account a USING (statement, account_code)
LEFT JOIN documents d ON d.sha256 = f.source_doc_sha256;
"""


def build_duckdb(gold_dir: Path = GOLD_DIR, silver_table: Path = SILVER_TABLE,
                 manifest: Path = ROOT / "data" / "_state" / "manifest.parquet", db_path: Path = DUCKDB_PATH,
                 root: Path = ROOT) -> None:
    """Tạo lại mwg.duckdb: view delta_scan (đường dẫn tương đối từ root) + view KPI. Chạy với cwd bất kỳ."""
    rel = lambda p: Path(p).resolve().relative_to(Path(root).resolve()).as_posix()  # noqa: E731
    tmp = Path(db_path).with_suffix(".tmp.duckdb")
    tmp.unlink(missing_ok=True)
    con = duckdb.connect(str(tmp))
    try:
        con.sql("INSTALL delta; LOAD delta;")
        sources = {name: f"delta_scan('{rel(Path(gold_dir) / name)}')"
                   for name in ("fact_financial", "dim_date", "dim_account", "dim_chain")}
        sources["silver_fin_statement_long"] = f"delta_scan('{rel(silver_table)}')"
        sources["documents"] = f"read_parquet('{rel(manifest)}')"
        cwd = Path.cwd()
        import os
        os.chdir(root)  # view tham chiếu đường dẫn tương đối: tạo và kiểm tra từ gốc repo
        try:
            for name, src in sources.items():
                con.sql(f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM {src}")
            con.sql(KPI_SQL)
            con.sql("SELECT count(*) FROM v_kpi_quarter").fetchall()  # view hỏng thì lỗi ở đây, trước khi thay file
        finally:
            os.chdir(cwd)
    finally:
        con.close()
    tmp.replace(db_path)


def export_snapshot(gold_dir: Path = GOLD_DIR, silver_table: Path = SILVER_TABLE,
                    manifest: Path = ROOT / "data" / "_state" / "manifest.parquet",
                    db_path: Path = SNAPSHOT_PATH) -> int:
    """Gold + Silver + danh sách tài liệu -> một file DuckDB tự chứa (bảng thật, cùng view KPI). Trả số byte."""
    tables = {name: pl.read_delta(str(Path(gold_dir) / name))
              for name in ("fact_financial", "dim_date", "dim_account", "dim_chain")}
    tables["silver_fin_statement_long"] = pl.read_delta(str(silver_table)).drop("row_hash", "loaded_at", strict=False)
    docs = pl.read_parquet(manifest)
    tables["documents"] = docs.select([c for c in MANIFEST_COLS if c in docs.columns])
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".tmp.duckdb")
    tmp.unlink(missing_ok=True)
    con = duckdb.connect(str(tmp))
    try:
        for name, df in tables.items():
            con.register("_src", df.to_arrow())
            con.execute(f"CREATE TABLE {name} AS SELECT * FROM _src")
            con.unregister("_src")
        con.execute(KPI_SQL)
        con.execute("SELECT count(*) FROM v_kpi_quarter").fetchall()  # view hỏng thì lỗi trước khi thay file
        con.execute("CHECKPOINT")
    finally:
        con.close()
    tmp.replace(db_path)
    return db_path.stat().st_size


def run() -> dict:
    silver = pl.read_delta(str(SILVER_TABLE))
    res = write_gold(silver)
    build_duckdb()
    size = export_snapshot()
    print(f"[gold] snapshot dashboard: {SNAPSHOT_PATH.relative_to(ROOT)} ({size / 1e6:.1f} MB)")
    print(f"[gold] fact_financial v{res['version']}: {res['fact']} dòng; {DUCKDB_PATH.relative_to(ROOT)} đã tạo lại")
    return res


if __name__ == "__main__":
    from src.common.lock import run_locked
    sys.exit(run_locked(run))
