"""Demo Delta time travel trên Silver: nạp BCTC năm kiểm toán rồi in khác biệt giữa hai phiên bản.

    python tools/demo_time_travel.py                 # kỳ 2025, bảng demo trong thư mục tạm
    python tools/demo_time_travel.py --period 2024

Chạy trên một bảng Delta riêng (thư mục tạm), không đụng data/silver/fin_statement_long:
  v0/v1: Silver dựng từ mọi tài liệu TRỪ BCTC năm (kiểm toán) của kỳ đó -> ngày 31/12 lấy từ BCTC Q4 (tự lập);
  v2:    MERGE thêm BCTC năm -> ô cùng khóa được thay vì source_priority 3 > 1 (quy tắc chọn bản thắng), và
         Q4 B02 đổi từ số in trên BCTC Q4 tự lập sang năm kiểm toán − 9T (vd. Q4/2025 doanh thu 42.850 -> 42.321 tỷ);
rồi đọc lại hai phiên bản bằng DeltaTable(version=...) / pl.read_delta(version=...) và so từng ô.
"""
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import polars as pl
from deltalake import DeltaTable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.transform import bronze_to_silver as S  # noqa: E402


def silver_from(cands: pl.DataFrame) -> pl.DataFrame:
    return S.build_silver(cands)[0]


def diff_versions(path: Path, v_old: int, v_new: int) -> pl.DataFrame:
    old = pl.read_delta(str(path), version=v_old)
    new = pl.read_delta(str(path), version=v_new)
    cols = ["amount_vnd", "source_period", "source_priority", "source_lang", "is_derived"]
    m = old.select(S.KEY + cols).join(new.select(S.KEY + cols), on=S.KEY, how="full", suffix="_new", coalesce=True)
    return m.filter(pl.col("amount_vnd").ne_missing(pl.col("amount_vnd_new"))
                    | pl.col("source_period").ne_missing(pl.col("source_period_new")))


def main(argv: list[str]) -> int:
    period = argv[argv.index("--period") + 1] if "--period" in argv else "2025"
    cands, _, _ = S.build_candidates()
    audited = cands.filter(pl.col("source_period") == period)
    if audited.is_empty():
        print(f"[silver] Không có BCTC năm {period} trong Silver")
        return 1
    path = Path(tempfile.mkdtemp(prefix="mwg_time_travel_")) / "fin_statement_long"
    S.merge_silver(silver_from(cands.filter(pl.col("source_period") != period)), path)
    v_before = DeltaTable(str(path)).version()
    res = S.merge_silver(silver_from(cands), path, full=False)
    v_after = DeltaTable(str(path)).version()
    print(f"[silver] Bảng demo: {path}")
    for h in sorted(DeltaTable(str(path)).history(), key=lambda h: h["version"]):
        print(f"  v{h['version']}: {h.get('operation'):<15} {datetime.fromtimestamp(h['timestamp'] / 1000):%Y-%m-%d %H:%M:%S}")
    print(f"[silver] MERGE nạp BCTC năm {period}: thêm {res.get('num_target_rows_inserted')}, "
          f"sửa {res.get('num_target_rows_updated')}")
    d = diff_versions(path, v_before, v_after)
    changed = d.filter(pl.col("amount_vnd").is_not_null() & pl.col("amount_vnd_new").is_not_null())
    print(f"[silver] Khác biệt v{v_before} -> v{v_after}: {d.height} ô "
          f"({changed.height} ô đổi bản thắng, {d.height - changed.height} ô mới)")
    show = changed.filter(pl.col("amount_vnd") != pl.col("amount_vnd_new")).sort("statement", "account_code")
    pl.Config.set_tbl_rows(20)
    pl.Config.set_tbl_width_chars(160)
    print("Ô đổi số (bản Q4 tự lập -> bản năm kiểm toán):")
    print(show.select("statement", "account_code", "period_kind", "amount_vnd", "source_period", "amount_vnd_new",
                      "source_period_new", (pl.col("amount_vnd_new") - pl.col("amount_vnd")).alias("chenh_lech")).head(20))
    q4 = show.filter((pl.col("statement") == "B02") & (pl.col("fiscal_quarter") == 4) & (pl.col("period_kind") == "quarter")
                     & pl.col("account_code").is_in(["10", "11", "20", "50", "60", "61"]))
    print(f"Q4/{period} (B02 quý 4): số in trên BCTC Q4 tự lập -> BCTC năm kiểm toán − 9T (is_derived):")
    print(q4.select("account_code", (pl.col("amount_vnd") / 1e9).round(1).alias("q4_tu_lap_ty"),
                    (pl.col("amount_vnd_new") / 1e9).round(1).alias("nam_kiem_toan_tru_9T_ty"),
                    ((pl.col("amount_vnd_new") - pl.col("amount_vnd")) / 1e9).round(1).alias("chenh_lech_ty"),
                    "is_derived", "is_derived_new"))
    same = changed.height - show.height
    print(f"{same} ô chỉ đổi tài liệu nguồn (số giống nhau, bản kiểm toán thay bản Q4).")
    print(f"Đọc lại phiên bản cũ: pl.read_delta('{path}', version={v_before})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
