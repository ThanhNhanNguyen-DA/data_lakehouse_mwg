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
                dq_balance_ok=True, is_trusted=True, gold_eligible=True, semantic_break=False, derived_from=None,
                gold_basis="critical")
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


def test_xac_nhan_cheo_hai_tai_lieu_va_phep_tinh():
    blocked = dict(is_trusted=False, gold_eligible=False, gold_basis=None)
    rows = [  # H1 bị chặn ở BCTC bán niên; BCTC Q2 cột lũy kế in cùng số -> 2 tài liệu khớp
        row(amount_vnd=737, source_doc_sha256="h" * 64, **blocked),
        row(amount_vnd=737, source_doc_sha256="q" * 64, source_period="2025-Q2", **blocked),
        # Q2 quý này chỉ một tài liệu, nhưng H1 (tài liệu khác) - Q1 (tài liệu khác) = 376 -> khớp
        row(fiscal_quarter=2, period_kind="quarter", amount_vnd=376, source_doc_sha256="q" * 64, **blocked),
        row(fiscal_quarter=1, period_kind="quarter", amount_vnd=361, source_doc_sha256="1" * 64),
        # 9T và Q3 cùng một tài liệu: 9T - Q3 = H1 chỉ xác nhận H1 (tài liệu khác), không tự xác nhận
        row(fiscal_quarter=3, period_kind="ytd", amount_vnd=1136, source_doc_sha256="3" * 64, **blocked),
        row(fiscal_quarter=3, period_kind="quarter", amount_vnd=399, source_doc_sha256="3" * 64, **blocked),
        # một tài liệu đọc lệch: không ai xác nhận
        row(account_code="20", amount_vnd=5, source_doc_sha256="h" * 64, **blocked)]
    df = frame(rows)
    got = dict(zip(zip(df["fiscal_quarter"], df["period_kind"], df["account_code"], df["source_doc_sha256"].str.slice(0, 1)),
                   S.cross_doc_confirmed(df)))
    assert got[(2, "ytd", "10", "h")] and got[(2, "ytd", "10", "q")] and got[(2, "quarter", "10", "q")]
    assert not got[(3, "ytd", "10", "3")] and not got[(3, "quarter", "10", "3")] and not got[(2, "ytd", "20", "h")]
    out = S.apply_cross_doc(df)
    assert out.filter(pl.col("gold_basis") == "cross_doc_consensus").height == 3
    assert out.filter(pl.col("gold_basis") == "critical").height == 1  # Q1 vốn đã qua critical


def test_q4_tu_nam_kiem_toan_thang_q4_tu_lap():
    assert S.rank_score(False, True, "vi-VN", 2, audited_q4=True) > S.rank_score(False, False, "vi-VN", 3)
    assert S.rank_score(False, True, "vi-VN", 2, audited_q4=True) < S.rank_score(False, False, "vi-VN", 1) + 1000
    fy = row(fiscal_quarter=0, period_kind="year", amount_vnd=1559, source_period="2025", source_priority=3,
             source_doc_sha256="f" * 64)
    nine = row(fiscal_quarter=3, period_kind="ytd", amount_vnd=1136, source_period="2025-Q3", source_doc_sha256="3" * 64)
    q4_printed = row(fiscal_quarter=4, period_kind="quarter", amount_vnd=428, source_period="2025-Q4", source_priority=1,
                     source_doc_sha256="4" * 64)
    q4_ytd = row(fiscal_quarter=0, period_kind="year", amount_vnd=1564, source_period="2025-Q4", source_priority=1,
                 source_doc_sha256="4" * 64)
    final, _ = S.build_silver(frame([fy, nine, q4_printed, q4_ytd]))
    q4 = final.filter((pl.col("fiscal_quarter") == 4) & (pl.col("period_kind") == "quarter")).row(0, named=True)
    assert q4["amount_vnd"] == 1559 - 1136 and q4["is_derived"]
    adj = S.audit_adjustments(frame([fy, q4_ytd]))
    assert adj.select("q4_ytd", "audited").row(0) == (1564, 1559)


def test_xac_nhan_cheo_khong_thang_so_da_qua_critical():
    blocked = dict(is_trusted=False, gold_eligible=False, gold_basis=None, is_comparative=True)
    df = frame([row(statement="B01", fiscal_quarter=4, period_kind="instant", account_code="410", amount_vnd=12_143,
                    source_doc_sha256="f" * 64),
                # hai báo cáo cùng bố cục cùng gán nhầm số của 411 vào 410
                row(statement="B01", fiscal_quarter=4, period_kind="instant", account_code="410", amount_vnd=4_435,
                    source_doc_sha256="a" * 64, **blocked),
                row(statement="B01", fiscal_quarter=4, period_kind="instant", account_code="410", amount_vnd=4_435,
                    source_doc_sha256="b" * 64, **blocked)])
    out = S.apply_cross_doc(df)
    assert out.filter(pl.col("amount_vnd") == 4_435)["gold_eligible"].to_list() == [False, False]


def test_o_thua_mau_thuan_6t_lam_bang_chung_nhung_khong_thang():
    blocked = dict(is_trusted=False, gold_eligible=False, gold_basis=None)
    rows = [row(fiscal_quarter=1, period_kind="quarter", amount_vnd=400, source_doc_sha256="1" * 64, **blocked),
            row(amount_vnd=1001, source_doc_sha256="h" * 64),                                    # H1 (thắng)
            row(amount_vnd=1000, source_doc_sha256="q" * 64, source_period="2025-Q2", **blocked),  # Q2 lũy kế (thua)
            row(fiscal_quarter=2, period_kind="quarter", amount_vnd=600, source_doc_sha256="q" * 64,
                source_period="2025-Q2", **blocked)]                                             # Q2 quý này (thua)
    df = frame(rows).with_columns(pl.Series("conflict_lost", [False, False, True, True]))
    final, _ = S.build_silver(df)
    got = {(r["fiscal_quarter"], r["period_kind"]): r for r in final.iter_rows(named=True)}
    assert got[(1, "quarter")]["gold_basis"] == "cross_doc_consensus"  # 1000 − 600 (cùng tài liệu thua) = 400
    assert got[(2, "ytd")]["amount_vnd"] == 1001
    assert got[(2, "quarter")]["amount_vnd"] == 601 and got[(2, "quarter")]["is_derived"]  # H1 − Q1, không lấy 600
    assert "conflict_lost" not in final.columns


def test_dung_lai_toan_bo_thay_ban_hang_cao_da_bi_loai(tmp_path):
    path = tmp_path / "silver"
    S.merge_silver(frame([row(amount_vnd=600)]), path)                          # bản in (hạng cao) lần trước
    derived = row(amount_vnd=601, is_derived=True, value_origin="derived")      # lần này bản in bị loại, chỉ còn suy ra
    S.merge_silver(frame([derived]), path, full=False)                          # nạp một phần: không đè hạng cao
    assert pl.read_delta(str(path))["amount_vnd"].to_list() == [600]
    S.merge_silver(frame([derived]), path)                                      # dựng lại toàn bộ: nguồn là sự thật
    assert pl.read_delta(str(path))["amount_vnd"].to_list() == [601]
