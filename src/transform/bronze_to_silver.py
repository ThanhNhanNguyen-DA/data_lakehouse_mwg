"""silver/fs_extracted_raw (+ manifest) -> silver/fin_statement_long (Delta MERGE).

    python -m src.transform.bronze_to_silver

Tên giữ theo CLAUDE.md; đầu vào thực tế là bảng ô đã bóc (fs_extracted_raw, override confirmed đã áp dụng ở bước
bóc tách, trước khi chọn bản thắng) và manifest (assurance_level -> source_priority).

Mỗi ô của báo cáo được quy về kỳ thật (map_period):
  B01  closing -> ngày cuối kỳ báo cáo; opening -> 31/12 năm trước (is_comparative)      period_kind = instant
  B02/B03 quý này -> quarter; lũy kế/bán niên -> ytd; năm (hoặc lũy kế 12 tháng) -> year (fiscal_quarter = 0)
  cột năm trước -> cùng kỳ năm trước, is_comparative = true
B01 dùng fiscal_quarter của ngày (31/12 -> 4), nên BCTC năm và BCTC Q4 cùng khóa và được xếp hạng với nhau.
Kỳ suy ra (is_derived): Q2 = H1 - Q1, Q3 = 9T - H1, Q4 = năm - 9 tháng, chỉ B02/B03 và mã cộng được theo thời gian.
"""
from __future__ import annotations

import csv
import hashlib
import sys
from datetime import date, datetime

import polars as pl
import pyarrow as pa
from deltalake import DeltaTable, write_deltalake

from src.common.paths import CONFIG_DIR, ROOT, SILVER_DIR, STATE_DIR

RAW_TABLE = SILVER_DIR / "fs_extracted_raw"
SILVER_TABLE = SILVER_DIR / "fin_statement_long"
UNMAPPED = STATE_DIR / "unmapped_items.csv"
DQ_REPORT = ROOT / "docs" / "dq_report.md"
MARK_START, MARK_END = "<!-- silver:start -->", "<!-- silver:end -->"
SYMBOL = "MWG"
KEY = ["symbol", "statement", "fiscal_year", "fiscal_quarter", "period_kind", "account_code"]
RESTATE_TOL = 0.001  # > 0,1% giữa số gốc và số so sánh: kỳ bị trình bày lại
# Không cộng/trừ được theo thời gian: lãi trên cổ phiếu (B02 70, 71), tiền đầu kỳ/cuối kỳ (B03 60, 70)
NOT_TIME_ADDITIVE = {("B02", "70"), ("B02", "71"), ("B03", "60"), ("B03", "70")}

SCHEMA = pa.schema([
    ("symbol", pa.string()), ("statement", pa.string()), ("fiscal_year", pa.int32()),
    ("fiscal_quarter", pa.int32()), ("period_kind", pa.string()), ("period_end", pa.date32()),
    ("account_code", pa.string()), ("printed_code", pa.string()), ("regime", pa.string()),
    ("item_name", pa.string()), ("amount_vnd", pa.int64()), ("source_priority", pa.int32()),
    ("source_lang", pa.string()), ("is_comparative", pa.bool_()), ("is_derived", pa.bool_()),
    ("is_manual", pa.bool_()), ("source_doc_sha256", pa.string()), ("source_period", pa.string()),
    ("page", pa.int32()), ("value_origin", pa.string()), ("dq_balance_ok", pa.bool_()),
    ("is_trusted", pa.bool_()), ("gold_eligible", pa.bool_()), ("semantic_break", pa.bool_()),
    ("derived_from", pa.string()), ("rank_score", pa.int32()), ("row_hash", pa.string()),
    ("loaded_at", pa.timestamp("us")),
])
CONSTRAINTS = {
    "statement_ok": "statement IN ('B01', 'B02', 'B03')",
    "quarter_ok": "fiscal_quarter BETWEEN 0 AND 4",
    "kind_ok": "period_kind IN ('instant', 'quarter', 'ytd', 'year')",
    "amount_not_null": "amount_vnd IS NOT NULL",
    "lang_ok": "source_lang IN ('vi-VN', 'en-US')",
}


# ---------- Quy kỳ và xếp hạng (hàm thuần, có test) ----------

def map_period(period: str, statement: str, kind: str) -> tuple[int, int, str, bool] | None:
    """(fiscal_year, fiscal_quarter, period_kind, is_comparative) của một cột báo cáo; None = cột không nhận ra."""
    y, part = int(period[:4]), period[5:]
    q = 4 if part == "" else 2 if part == "H1" else int(part[1])
    if statement == "B01":
        return {"closing": (y, q, "instant", False), "opening": (y - 1, 4, "instant", True)}.get(kind)
    prior = kind in ("prior", "q_prior", "ytd_prior")
    yy = y - 1 if prior else y
    if kind in ("q_current", "q_prior"):
        return yy, q, "quarter", prior
    if kind in ("current", "prior", "ytd_current", "ytd_prior"):  # năm, bán niên, B03 quý: lũy kế từ đầu năm
        # lũy kế 12 tháng = năm; lũy kế Q1 = chính quý 1
        return (yy, 0, "year", prior) if q == 4 else (yy, 1, "quarter", prior) if q == 1 else (yy, q, "ytd", prior)
    return None


def period_end(year: int, quarter: int) -> date:
    return date(year, 12, 31) if quarter in (0, 4) else date(year, 3 * quarter, 30 if quarter in (2, 3) else 31)


def rank_score(is_comparative: bool, is_derived: bool, lang: str, priority: int) -> int:
    """Thứ tự bản thắng của CLAUDE.md gói vào một số: tiêu chí trước nặng hơn mọi tiêu chí sau cộng lại."""
    return (not is_comparative) * 1000 + (not is_derived) * 100 + (lang == "vi-VN") * 10 + int(priority or 0)


def _rank_expr() -> pl.Expr:
    return ((~pl.col("is_comparative")).cast(pl.Int32) * 1000 + (~pl.col("is_derived")).cast(pl.Int32) * 100
            + (pl.col("source_lang") == "vi-VN").cast(pl.Int32) * 10 + pl.col("source_priority")).cast(pl.Int32)


def pick_winners(df: pl.DataFrame) -> pl.DataFrame:
    """Một dòng mỗi khóa: rank_score cao nhất; hòa thì ô gold_eligible, rồi sha256 (ổn định giữa các lần chạy)."""
    return (df.sort(["rank_score", "gold_eligible", "source_doc_sha256"], descending=[True, True, False])
              .unique(subset=KEY, keep="first", maintain_order=True))


# ---------- Dựng bảng dài ----------

def _dim_rows() -> list[dict]:
    with (CONFIG_DIR / "dim_account.csv").open(encoding="utf-8", newline="") as f:
        return [r for r in csv.DictReader(f) if r["regime"] == "TT200"]


def _dim_codes() -> set[tuple[str, str]]:
    return {(r["statement"], r["account_code"]) for r in _dim_rows()}


def _negative_codes() -> set[tuple[str, str]]:
    """Mã chuẩn có sign = -1 (khoản giảm trừ, dự phòng, hao mòn): Silver lưu số âm, bất kể báo cáo in dấu nào."""
    return {(r["statement"], r["account_code"]) for r in _dim_rows() if r["sign"] == "-1"}


def build_long(raw: pl.DataFrame, manifest: pl.DataFrame, dim_codes: set[tuple[str, str]],
               negative: set[tuple[str, str]] = frozenset()) -> tuple[pl.DataFrame, pl.DataFrame, dict]:
    """-> (ứng viên Silver chưa chọn bản thắng, ô không ánh xạ được, thống kê loại bỏ).

    negative: mã chuẩn lưu số âm. TT200 in khoản giảm trừ trong ngoặc, TT99 in dương, và OCR hay mất ngoặc: không
    chuẩn hóa thì số gốc và số so sánh lệch dấu, Q2 = H1 - Q1 sai khi hai báo cáo in khác dấu."""
    stats = {}
    df = raw.filter(pl.col("amount_vnd").is_not_null())
    stats["null"] = raw.height - df.height
    keep = pl.col("period_check_ok") & (pl.col("period") != "pending") & (pl.col("period").str.slice(0, 4).str.contains(r"^\d{4}$"))
    # nhãn cột B02 TT99 không chứng minh được: bỏ B02 của báo cáo đó, B01/B03 không phụ thuộc nhãn cột B02
    keep &= ~((pl.col("statement") == "B02") & ~pl.col("columns_ok"))
    stats["dq_excluded"] = df.filter(~keep).height
    df = df.filter(keep)
    # mã chuẩn: TT99 qua bảng map; B01/B02 phải có trong dim_account (mã OCR sai không được bịa thành mã thật)
    known = pl.struct("statement", "canonical_code").map_elements(
        lambda s: s["statement"] == "B03" or (s["statement"], s["canonical_code"]) in dim_codes, return_dtype=pl.Boolean)
    mapped = pl.col("canonical_code").is_not_null() & known
    unmapped = df.filter(~mapped)
    df = df.filter(mapped)

    pri = manifest.select(pl.col("sha256").alias("source_doc_sha256"), pl.col("assurance_level").cast(pl.Int32).alias("source_priority"))
    df = df.join(pri, on="source_doc_sha256", how="left").with_columns(pl.col("source_priority").fill_null(1))
    periods = df.select("period", "statement", "period_kind").unique()
    mp = pl.DataFrame([dict(zip(("period", "statement", "period_kind"), k)) | dict(zip(
        ("fiscal_year", "fiscal_quarter", "kind", "is_comparative"), map_period(*k) or (None, None, None, None)))
        for k in periods.iter_rows()], schema={"period": pl.Utf8, "statement": pl.Utf8, "period_kind": pl.Utf8,
                                               "fiscal_year": pl.Int32, "fiscal_quarter": pl.Int32, "kind": pl.Utf8,
                                               "is_comparative": pl.Boolean})
    df = df.join(mp, on=["period", "statement", "period_kind"], how="left").filter(pl.col("kind").is_not_null())
    df = df.with_columns(pl.struct("fiscal_year", "fiscal_quarter").map_elements(
        lambda s: period_end(s["fiscal_year"], s["fiscal_quarter"]), return_dtype=pl.Date).alias("period_end"))
    # mã TT99 đổi nội dung (semantic_break) mà rơi vào ngày còn theo TT200 (cột đầu năm 2026): không cùng nghĩa với
    # số TT200 cùng khóa nên không đưa vào (giữ ở fs_extracted_raw để truy vết)
    stale = (pl.col("regime") == "TT99") & pl.col("semantic_break") & (pl.col("period_end") < date(2026, 1, 1))
    stats["semantic_break_dropped"] = df.filter(stale).height
    df = df.filter(~stale)
    neg = pl.struct("statement", "canonical_code").map_elements(
        lambda r: (r["statement"], r["canonical_code"]) in negative, return_dtype=pl.Boolean)
    df = df.with_columns(pl.when(neg).then(-pl.col("amount_vnd").abs()).otherwise(pl.col("amount_vnd")).alias("amount_vnd"))
    out = df.select(
        pl.lit(SYMBOL).alias("symbol"), "statement", "fiscal_year", "fiscal_quarter", pl.col("kind").alias("period_kind"),
        "period_end", pl.col("canonical_code").alias("account_code"), pl.col("account_code").alias("printed_code"),
        "regime", "item_name", "amount_vnd", "source_priority", pl.col("lang").alias("source_lang"), "is_comparative",
        pl.lit(False).alias("is_derived"), "is_manual", "source_doc_sha256", pl.col("period").alias("source_period"),
        "page", "value_origin", "dq_balance_ok", "is_trusted", "gold_eligible",
        # TT99 mã đổi nội dung: đánh dấu mọi kỳ TT99 để Gold/KPI không nối chuỗi qua mốc đổi chuẩn
        ((pl.col("regime") == "TT99") & pl.col("account_code").is_in(_break_codes())).alias("semantic_break"),
        pl.lit(None, pl.Utf8).alias("derived_from"))
    return out.with_columns(_rank_expr().alias("rank_score")), unmapped, stats


def _break_codes() -> list[str]:
    with (CONFIG_DIR / "account_code_regime_map.csv").open(encoding="utf-8", newline="") as f:
        return [r["account_code"] for r in csv.DictReader(f) if (r.get("semantic_break") or "").lower() == "true"]


def derive_quarters(win: pl.DataFrame) -> pl.DataFrame:
    """Q2 = H1 - Q1, Q3 = 9T - H1, Q4 = năm - 9T (B02/B03, mã cộng được theo thời gian) từ các bản thắng."""
    flows = win.filter(pl.col("statement").is_in(["B02", "B03"]) & ~pl.struct("statement", "account_code").map_elements(
        lambda s: (s["statement"], s["account_code"]) in NOT_TIME_ADDITIVE, return_dtype=pl.Boolean))
    j = ["statement", "account_code", "fiscal_year"]

    def pick(q: int, kinds: list[str]) -> pl.DataFrame:
        return flows.filter((pl.col("fiscal_quarter") == q) & pl.col("period_kind").is_in(kinds))

    parts = [(2, pick(2, ["ytd"]), pick(1, ["quarter"])), (3, pick(3, ["ytd"]), pick(2, ["ytd"])),
             (4, pick(0, ["year"]), pick(3, ["ytd"]))]
    out = []
    for q, a, b in parts:
        m = a.join(b, on=j, suffix="_b")
        if m.is_empty():
            continue
        out.append(m.select(
            "symbol", "statement", "fiscal_year", pl.lit(q, pl.Int32).alias("fiscal_quarter"),
            pl.lit("quarter").alias("period_kind"), "account_code", "printed_code", "regime", "item_name",
            (pl.col("amount_vnd") - pl.col("amount_vnd_b")).alias("amount_vnd"),
            pl.min_horizontal("source_priority", "source_priority_b").alias("source_priority"),
            pl.when((pl.col("source_lang") == "vi-VN") & (pl.col("source_lang_b") == "vi-VN")).then(pl.lit("vi-VN"))
              .otherwise(pl.lit("en-US")).alias("source_lang"),
            (pl.col("is_comparative") | pl.col("is_comparative_b")).alias("is_comparative"),
            pl.lit(True).alias("is_derived"), (pl.col("is_manual") | pl.col("is_manual_b")).alias("is_manual"),
            "source_doc_sha256", "source_period", pl.lit(None, pl.Int32).alias("page"), pl.lit("derived").alias("value_origin"),
            (pl.col("dq_balance_ok") & pl.col("dq_balance_ok_b")).alias("dq_balance_ok"),
            (pl.col("is_trusted") & pl.col("is_trusted_b")).alias("is_trusted"),
            (pl.col("gold_eligible") & pl.col("gold_eligible_b")).alias("gold_eligible"),
            (pl.col("semantic_break") | pl.col("semantic_break_b")).alias("semantic_break"),
            pl.format("{}:{} - {}:{}", "source_period", "source_doc_sha256", "source_period_b", "source_doc_sha256_b")
              .alias("derived_from")))
    if not out:
        return win.clear()
    d = pl.concat(out).with_columns(pl.struct("fiscal_year", "fiscal_quarter").map_elements(
        lambda s: period_end(s["fiscal_year"], s["fiscal_quarter"]), return_dtype=pl.Date).alias("period_end"))
    return d.with_columns(_rank_expr().alias("rank_score")).select(win.columns)


def restatements(cands: pl.DataFrame) -> pl.DataFrame:
    """Cùng ô có số gốc và số so sánh (không suy ra), cả hai trusted, lệch > 0,1%: kỳ bị trình bày lại."""
    base = cands.filter(~pl.col("is_derived") & pl.col("is_trusted"))
    orig = pick_winners(base.filter(~pl.col("is_comparative")))
    comp = base.filter(pl.col("is_comparative"))
    m = orig.join(comp, on=KEY, suffix="_c")
    return (m.filter((pl.col("amount_vnd") - pl.col("amount_vnd_c")).abs()
                     > RESTATE_TOL * pl.max_horizontal(pl.col("amount_vnd").abs(), pl.lit(1)))
             .select("statement", "fiscal_year", "fiscal_quarter", "period_kind", "account_code", "amount_vnd",
                     "amount_vnd_c", "source_period", "source_period_c", "source_lang", "source_lang_c")
             .unique().sort("fiscal_year", "fiscal_quarter", "statement", "account_code"))


def _row_hash(df: pl.DataFrame) -> pl.Series:
    cols = [c for c in df.columns if c not in ("row_hash", "loaded_at")]
    return pl.Series("row_hash", [hashlib.md5(repr(r).encode()).hexdigest() for r in df.select(cols).iter_rows()])


def to_arrow(df: pl.DataFrame) -> pa.Table:
    """Ép đúng schema Silver; cột sai kiểu (vd. amount_vnd là chuỗi) -> lỗi ngay, không ghi."""
    df = df.with_columns(_row_hash(df), pl.lit(datetime.now()).cast(pl.Datetime("us")).alias("loaded_at"))
    return df.select([f.name for f in SCHEMA]).to_arrow().cast(SCHEMA, safe=True)


def merge_silver(df: pl.DataFrame, path=SILVER_TABLE, full: bool = True) -> dict:
    """Delta MERGE theo KEY: bản mới chỉ ghi đè khi rank_score >= bản cũ và nội dung khác (row_hash).

    full=True: nguồn là toàn bộ Silver -> xóa khóa không còn trong nguồn. full=False: nạp lại một phần (vd. một kỳ)."""
    src = to_arrow(df)
    if not DeltaTable.is_deltatable(str(path)):
        write_deltalake(str(path), src, mode="overwrite")
        DeltaTable(str(path)).alter.add_constraint(CONSTRAINTS)
        return {"num_target_rows_inserted": src.num_rows, "created": True}
    on = " AND ".join(f"t.{k} = s.{k}" for k in KEY)
    m = (DeltaTable(str(path)).merge(source=src, predicate=on, source_alias="s", target_alias="t")
         .when_matched_update_all(predicate="s.rank_score >= t.rank_score AND s.row_hash <> t.row_hash")
         .when_not_matched_insert_all())
    if full:
        m = m.when_not_matched_by_source_delete()
    return m.execute()


# ---------- Ghi phụ ----------

def write_unmapped(unmapped: pl.DataFrame) -> int:
    """Một dòng mỗi (regime, báo cáo, mã) không ánh xạ được, kèm lý do (CLAUDE.md: không bịa mã)."""
    g = (unmapped.group_by("regime", "statement", "account_code")
         .agg(pl.col("item_name").first(), pl.len().alias("n_cells"),
              pl.col("period").unique().sort().str.join(";").alias("periods"),
              pl.col("canonical_code").first())
         .sort("regime", "statement", "account_code"))

    def reason(r: dict) -> str:
        if r["regime"] == "TT99" and r["statement"] == "B03":
            return "B03 theo TT99: chưa có mẫu B03 trong docs/reference nên chưa có bảng map; B03 chưa vào Gold"
        if r["periods"] and all(x.startswith("2014") for x in r["periods"].split(";")):
            return "BCTC 2014 lập theo mẫu trước TT200 (mã số khác): chưa có bảng map cho mẫu cũ"
        if r["regime"] == "TT99" and r["canonical_code"] is None:
            return "mã TT99 chưa ghép được với mã TT200 (tên khác, số 31/12/2025 không khớp duy nhất): xem docs/dim_account_notes.md"
        return "mã đọc được không có trong dim_account TT200: nghi OCR đọc sai mã; ô không đưa vào Silver"
    rows = [{**{k: r[k] for k in ("regime", "statement", "account_code", "item_name", "n_cells", "periods")},
             "reason": reason(r)} for r in g.iter_rows(named=True)]
    UNMAPPED.parent.mkdir(parents=True, exist_ok=True)
    with UNMAPPED.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["regime", "statement", "account_code", "item_name", "n_cells", "periods", "reason"])
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def write_report(restated: pl.DataFrame, stats: dict) -> None:
    lines = [MARK_START, "", f"### Kỳ bị trình bày lại – Silver (tự sinh, {datetime.now():%Y-%m-%d %H:%M})", "",
             "Ô có cả số gốc (báo cáo của chính kỳ đó) và số so sánh (cột kỳ trước của báo cáo kỳ sau), **cả hai trusted**, "
             f"lệch > 0,1%: {restated.height} ô. Silver giữ số gốc. Ô chưa trusted không so (lệch có thể do OCR).", "",
             f"Silver: {stats['rows']} dòng; loại {stats['null']} ô rỗng, {stats['dq_excluded']} ô của báo cáo không qua "
             f"kiểm tra kỳ/nhãn cột B02, {stats['unmapped_cells']} ô không ánh xạ được mã ({stats['unmapped']} mã, "
             f"`data/_state/unmapped_items.csv`), {stats['semantic_break_dropped']} ô TT99 đổi nội dung rơi vào ngày "
             f"theo TT200; thêm {stats['derived']} ô suy ra (Q2 = H1 − Q1, Q3 = 9T − H1, Q4 = năm − 9T).", ""]
    if restated.height:
        lines += ["| Kỳ | Báo cáo | Kỳ cột | Mã | Số gốc (VND) | Số so sánh (VND) | Chênh lệch | Tài liệu gốc | Tài liệu so sánh |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for r in restated.iter_rows(named=True):
            diff = r["amount_vnd_c"] - r["amount_vnd"]
            pct = f"{diff / r['amount_vnd']:.2%}" if r["amount_vnd"] else "–"
            lines.append(f"| {r['fiscal_year']}-{r['fiscal_quarter']} | {r['statement']} | {r['period_kind']} | "
                         f"{r['account_code']} | {r['amount_vnd']:,} | {r['amount_vnd_c']:,} | {diff:,} ({pct}) | "
                         f"{r['source_period']} {r['source_lang'][:2]} | {r['source_period_c']} {r['source_lang_c'][:2]} |")
    lines += ["", MARK_END]
    text = DQ_REPORT.read_text(encoding="utf-8")
    block = "\n".join(lines)
    if MARK_START in text:
        text = text[:text.index(MARK_START)] + block + text[text.index(MARK_END) + len(MARK_END):]
    else:  # đặt ngay sau bảng mẫu của mục 3
        anchor = "Bảng tự sinh nằm ở mục 5"
        i = text.index(anchor)
        text = text[:i] + block + "\n\n" + text[i:]
    DQ_REPORT.write_text(text, encoding="utf-8")


def build_candidates() -> tuple[pl.DataFrame, pl.DataFrame, dict]:
    from src.ingestion.mwg_crawler import load_manifest
    raw = pl.read_delta(str(RAW_TABLE))
    return build_long(raw, load_manifest(), _dim_codes(), _negative_codes())


def run() -> dict:
    cands, unmapped, stats = build_candidates()
    win = pick_winners(cands)
    derived = derive_quarters(win)
    final = pick_winners(pl.concat([cands, derived]))
    stats.update(rows=final.height, derived=derived.height, unmapped_cells=unmapped.height,
                 unmapped=write_unmapped(unmapped))
    res = merge_silver(final)
    restated = restatements(cands)
    write_report(restated, stats)
    v = DeltaTable(str(SILVER_TABLE)).version()
    print(f"[silver] fin_statement_long v{v}: {final.height} dòng ({stats['derived']} suy ra); "
          f"MERGE: thêm {res.get('num_target_rows_inserted', 0)}, sửa {res.get('num_target_rows_updated', 0)}, "
          f"xóa {res.get('num_target_rows_deleted', 0)}; trình bày lại {restated.height}; mã không ánh xạ {stats['unmapped']}")
    return stats


if __name__ == "__main__":
    run()
    sys.exit(0)
