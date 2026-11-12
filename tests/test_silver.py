"""Test Silver fin_statement_long: quy kỳ, xếp hạng bản thắng, kỳ suy ra, Delta MERGE, schema enforcement.

Số tiền là số giả lập (không phải số liệu MWG)."""
from datetime import date

import polars as pl
import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake

from src.transform import bronze_to_silver as S


@pytest.mark.parametrize("period,st,kind,expected", [
    ("2025", "B01", "closing", (2025, 4, "instant", False)),
    ("2025-Q3", "B01", "opening", (2024, 4, "instant", True)),
    ("2025-H1", "B01", "closing", (2025, 2, "instant", False)),
    ("2025", "B02", "current", (2025, 0, "year", False)),
    ("2025", "B02", "prior", (2024, 0, "year", True)),
    ("2025-H1", "B02", "current", (2025, 2, "ytd", False)),
    ("2025-H1", "B03", "prior", (2024, 2, "ytd", True)),
    ("2025-Q3", "B02", "q_current", (2025, 3, "quarter", False)),
    ("2025-Q3", "B02", "q_prior", (2024, 3, "quarter", True)),
    ("2025-Q3", "B02", "ytd_current", (2025, 3, "ytd", False)),
    ("2025-Q3", "B02", "current", (2025, 3, "ytd", False)),        # báo cáo quý hai cột: lũy kế
    ("2025-Q4", "B02", "ytd_current", (2025, 0, "year", False)),   # lũy kế 12 tháng = năm
    ("2025-Q1", "B02", "current", (2025, 1, "quarter", False)),    # lũy kế Q1 = quý 1
    ("2025-Q1", "B03", "prior", (2024, 1, "quarter", True)),
    ("2025", "B02", "khong_ro", None),
])
def test_map_period(period, st, kind, expected):
    assert S.map_period(period, st, kind) == expected


def test_period_end():
    assert S.period_end(2025, 0) == date(2025, 12, 31)
    assert S.period_end(2025, 1) == date(2025, 3, 31)
    assert S.period_end(2025, 2) == date(2025, 6, 30)
    assert S.period_end(2025, 3) == date(2025, 9, 30)


def test_rank_score_theo_thu_tu_claude_md():
    r = S.rank_score
    # tiêu chí trước thắng mọi tiêu chí sau cộng lại
    assert r(False, True, "en-US", 1) > r(True, False, "vi-VN", 3)
    assert r(False, False, "en-US", 1) > r(False, True, "vi-VN", 3)
    assert r(False, False, "vi-VN", 1) > r(False, False, "en-US", 3)
    assert r(False, False, "vi-VN", 3) > r(False, False, "vi-VN", 2)


def row(**kw) -> dict:
    base = dict(symbol="MWG", statement="B02", fiscal_year=2025, fiscal_quarter=2, period_kind="ytd",
                period_end=date(2025, 6, 30), account_code="10", printed_code="10", regime="TT200", item_name="x",
                amount_vnd=100, source_priority=2, source_lang="vi-VN", is_comparative=False, is_derived=False,
                is_manual=False, source_doc_sha256="a" * 64, source_period="2025-H1", page=9, value_origin="ocr",
                dq_balance_ok=True, is_trusted=True, gold_eligible=True, semantic_break=False, derived_from=None)
    base.update(kw)
    base["rank_score"] = S.rank_score(base["is_comparative"], base["is_derived"], base["source_lang"],
                                      base["source_priority"])
    return base


def frame(rows) -> pl.DataFrame:
    return pl.DataFrame(rows, schema_overrides={"fiscal_year": pl.Int32, "fiscal_quarter": pl.Int32,
                                                "source_priority": pl.Int32, "page": pl.Int32,
                                                "rank_score": pl.Int32, "derived_from": pl.Utf8})


def test_pick_winners_khong_de_ban_hang_thap_thang():
    df = frame([row(amount_vnd=1, is_comparative=True, source_priority=3, source_doc_sha256="c" * 64),
                row(amount_vnd=2, source_lang="en-US", source_doc_sha256="e" * 64),
                row(amount_vnd=3, source_doc_sha256="v" * 64)])
    w = S.pick_winners(df)
    assert w.height == 1 and w["amount_vnd"][0] == 3


def test_derive_quarters_q2_q3_q4_va_bo_ma_khong_cong_duoc():
    rows = [row(fiscal_quarter=1, period_kind="quarter", amount_vnd=10, source_doc_sha256="1" * 64),
            row(fiscal_quarter=2, period_kind="ytd", amount_vnd=25, source_doc_sha256="2" * 64),
            row(fiscal_quarter=3, period_kind="ytd", amount_vnd=45, source_doc_sha256="3" * 64, source_lang="en-US"),
            row(fiscal_quarter=0, period_kind="year", amount_vnd=70, source_doc_sha256="4" * 64, gold_eligible=False),
            row(fiscal_quarter=1, period_kind="quarter", amount_vnd=5, account_code="70"),   # EPS: không suy ra
            row(fiscal_quarter=2, period_kind="ytd", amount_vnd=9, account_code="70")]
    d = S.derive_quarters(frame(rows))
    got = {r["fiscal_quarter"]: r for r in d.iter_rows(named=True)}
    assert set(got) == {2, 3, 4} and all(r["account_code"] == "10" for r in d.iter_rows(named=True))
    assert got[2]["amount_vnd"] == 15 and got[3]["amount_vnd"] == 20 and got[4]["amount_vnd"] == 25
    assert all(r["is_derived"] and r["period_kind"] == "quarter" for r in got.values())
    assert got[3]["source_lang"] == "en-US" and got[2]["source_lang"] == "vi-VN"
    assert got[4]["gold_eligible"] is False and got[2]["gold_eligible"] is True
    assert "2025-H1" in got[2]["derived_from"]
    # số suy ra thua số in trên báo cáo cùng khóa
    printed = row(fiscal_quarter=2, period_kind="quarter", amount_vnd=16, source_doc_sha256="9" * 64)
    w = S.pick_winners(pl.concat([d, frame([printed])]))
    assert w.filter(pl.col("fiscal_quarter") == 2)["amount_vnd"][0] == 16


def _raw(**kw) -> dict:
    base = dict(source_doc_sha256="a" * 64, lang="vi-VN", period="2025-H1", regime="TT200", statement="B02",
                page=9, account_code="11", canonical_code="11", item_name="Giá vốn", period_kind="current",
                amount_vnd=500, is_manual=False, value_origin="ocr", dq_balance_ok=True, is_trusted=True,
                gold_eligible=True, semantic_break=False, period_check_ok=True, columns_ok=True)
    base.update(kw)
    return base


def test_build_long_dau_ma_khong_anh_xa_va_cot_b02_chua_chung_minh():
    raw = pl.DataFrame([_raw(), _raw(account_code="10", canonical_code="10", amount_vnd=900),
                        _raw(account_code="36", canonical_code="36"),                      # mã OCR sai
                        _raw(regime="TT99", account_code="99", canonical_code=None),       # TT99 chưa map
                        _raw(account_code="01", canonical_code="01", columns_ok=False),    # nhãn cột chưa chứng minh
                        _raw(statement="B01", account_code="270", canonical_code="270", period_kind="closing",
                             columns_ok=False, amount_vnd=7),
                        _raw(account_code="20", canonical_code="20", amount_vnd=None)])
    mf = pl.DataFrame({"sha256": ["a" * 64], "assurance_level": [2]})
    out, unmapped, stats = S.build_long(raw, mf, {("B02", "10"), ("B02", "11"), ("B02", "01"), ("B01", "270")},
                                        {("B02", "11")})
    got = {(r["statement"], r["account_code"]): r for r in out.iter_rows(named=True)}
    assert set(got) == {("B02", "11"), ("B02", "10"), ("B01", "270")}
    assert got[("B02", "11")]["amount_vnd"] == -500 and got[("B02", "10")]["amount_vnd"] == 900
    assert got[("B01", "270")]["period_kind"] == "instant" and got[("B01", "270")]["fiscal_quarter"] == 2
    assert got[("B02", "10")]["source_priority"] == 2 and got[("B02", "10")]["period_kind"] == "ytd"
    assert sorted(unmapped["account_code"].to_list()) == ["36", "99"]
    assert stats["null"] == 1 and stats["dq_excluded"] == 1


def test_merge_chay_hai_lan_khong_doi_va_khong_ghi_de_bang_ban_thap(tmp_path):
    path = tmp_path / "silver"
    S.merge_silver(frame([row(amount_vnd=100), row(account_code="11", amount_vnd=-50)]), path)
    S.merge_silver(frame([row(amount_vnd=100), row(account_code="11", amount_vnd=-50)]), path)
    assert DeltaTable(str(path)).version() == 1  # ghi lần đầu + constraint; lần 2 không có gì đổi
    # bản so sánh (hạng thấp) không ghi đè bản gốc
    S.merge_silver(frame([row(amount_vnd=999, is_comparative=True, source_doc_sha256="z" * 64)]), path, full=False)
    t = pl.read_delta(str(path))
    assert t.height == 2 and t.filter(pl.col("account_code") == "10")["amount_vnd"][0] == 100
    # cùng tài liệu, cùng hạng, số đổi (vd. override confirmed): cập nhật
    S.merge_silver(frame([row(amount_vnd=101, is_manual=True, value_origin="manual")]), path, full=False)
    t = pl.read_delta(str(path))
    assert t.height == 2 and t.filter(pl.col("account_code") == "10")["amount_vnd"][0] == 101
    # full: khóa không còn trong nguồn thì xóa
    S.merge_silver(frame([row(amount_vnd=101, is_manual=True, value_origin="manual")]), path)
    assert pl.read_delta(str(path)).height == 1


def test_schema_enforcement_tu_choi_sai_kieu_va_vi_pham_rang_buoc(tmp_path):
    path = tmp_path / "silver"
    S.merge_silver(frame([row()]), path)
    good = S.to_arrow(frame([row(account_code="11")]))
    bad_type = good.set_column(good.schema.get_field_index("amount_vnd"), "amount_vnd",
                               pa.array(["một trăm"], pa.string()))
    with pytest.raises(Exception):
        write_deltalake(str(path), bad_type, mode="append")       # Delta từ chối schema khác
    with pytest.raises(Exception):
        S.to_arrow(frame([row()]).with_columns(pl.lit("abc").alias("amount_vnd")))  # ép kiểu ở đường ghi của Silver
    bad_q = S.to_arrow(frame([row(account_code="12", fiscal_quarter=7)]))
    with pytest.raises(Exception):
        write_deltalake(str(path), bad_q, mode="append")          # CHECK fiscal_quarter BETWEEN 0 AND 4
    with pytest.raises(Exception):
        S.merge_silver(frame([row(account_code="13", period_kind="monthly")]), path, full=False)
    assert pl.read_delta(str(path)).height == 1
