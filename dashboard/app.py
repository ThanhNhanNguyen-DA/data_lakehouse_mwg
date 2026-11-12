"""Dashboard BCTC hợp nhất MWG (Streamlit + Plotly), đọc Gold qua DuckDB read_only.

    streamlit run dashboard/app.py

Không chạy logic transform ở đây: mọi số lấy từ view của data/gold/mwg.duckdb. Tiền hiển thị theo tỷ đồng.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import duckdb
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "gold" / "mwg.duckdb"
BILLION = 1e9
# Thứ tự màu cố định (bảng categorical đã kiểm định CVD của skill dataviz): màu theo chỉ tiêu, không theo thứ hạng
COLORS = {"revenue": "#2a78d6", "npat": "#eb6834", "gross": "#1baf7a", "net": "#eda100", "roe": "#e87ba4",
          "roa": "#6250d6"}
STACK = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
STATUS_COLORS = {"có số": "#1baf7a", "bị chặn DQ": "#eda100", "không có tài liệu gốc": "#d4d3cf"}
TT99_NOTE = "Đổi chuẩn TT99 (từ 2026): mã 120, 130 khác nội dung TT200, không nối chuỗi"

st.set_page_config(page_title="MWG Lakehouse", page_icon="📊", layout="wide")


@st.cache_resource
def connect() -> duckdb.DuckDBPyConnection:
    os.chdir(ROOT)  # view trong mwg.duckdb dùng đường dẫn tương đối từ gốc repo
    con = duckdb.connect(str(DB), read_only=True)
    con.execute("LOAD delta")
    return con


@st.cache_data(ttl=600)
def q(sql: str) -> pd.DataFrame:
    return connect().execute(sql).df()


def tỷ(v) -> str:
    return "–" if pd.isna(v) else f"{v / BILLION:,.0f} tỷ".replace(",", ".")


def pct(v) -> str:
    return "–" if pd.isna(v) else f"{v * 100:.1f}%".replace(".", ",")


def tt99_marker(fig: go.Figure, x) -> None:
    """Đường dọc + chú thích tại kỳ TT99 đầu tiên (biểu đồ có mã semantic_break)."""
    fig.add_vline(x=x, line_dash="dot", line_color="#8a8984", line_width=1)
    fig.add_annotation(x=x, y=0.98, yref="paper", text="đổi chuẩn TT99", showarrow=False, bgcolor="rgba(255,255,255,0.8)",
                       font=dict(size=11, color="#52514e"), xanchor="left")


def layout(fig: go.Figure, yfmt: str = "", height: int = 380) -> go.Figure:
    fig.update_layout(height=height, margin=dict(l=8, r=8, t=80, b=8), hovermode="x unified",
                      title=dict(y=0.97, yanchor="top", x=0, xanchor="left"),
                      legend=dict(orientation="h", y=1.0, yanchor="bottom", x=0), plot_bgcolor="rgba(0,0,0,0)",
                      paper_bgcolor="rgba(0,0,0,0)")
    fig.update_yaxes(tickformat=yfmt, gridcolor="rgba(128,128,128,0.18)", zeroline=False)
    fig.update_xaxes(showgrid=False, type="category")
    return fig


def quarter_frame() -> pd.DataFrame:
    """Mọi quý trong dim_date (kỳ thiếu số giữ dòng trống để biểu đồ không nối qua khoảng trống)."""
    return q("""SELECT d.fiscal_year, d.fiscal_quarter, d.quarter_label AS label, k.* EXCLUDE (fiscal_year, fiscal_quarter, quarter_label, period_end)
                FROM dim_date d LEFT JOIN v_kpi_quarter k USING (fiscal_year, fiscal_quarter)
                ORDER BY d.fiscal_year, d.fiscal_quarter""")


def year_frame() -> pd.DataFrame:
    return q("""SELECT y.fiscal_year, 'FY' || y.fiscal_year AS label, k.* EXCLUDE (fiscal_year)
                FROM (SELECT DISTINCT fiscal_year FROM dim_date) y LEFT JOIN v_kpi_year k USING (fiscal_year)
                ORDER BY 1""")


def first_tt99(df: pd.DataFrame):
    hit = df[df["regime"] == "TT99"]
    return hit["label"].iloc[0] if len(hit) else None


# ---------- Trang ----------

def page_overview():
    st.header("Tổng quan")
    qd = q("SELECT * FROM v_kpi_quarter WHERE net_revenue IS NOT NULL ORDER BY fiscal_year DESC, fiscal_quarter DESC LIMIT 1")
    yd = q("SELECT * FROM v_kpi_year WHERE net_revenue IS NOT NULL ORDER BY fiscal_year DESC LIMIT 1")
    if len(qd):
        r = qd.iloc[0]
        st.subheader(f"Quý gần nhất có số: {r['quarter_label']}")
        c = st.columns(5)
        c[0].metric("Doanh thu thuần", tỷ(r["net_revenue"]),
                    None if pd.isna(r["revenue_yoy"]) else f"{pct(r['revenue_yoy'])} so với cùng kỳ")
        c[1].metric("LN sau thuế", tỷ(r["npat"]), None if pd.isna(r["npat_yoy"]) else f"{pct(r['npat_yoy'])} so với cùng kỳ")
        c[2].metric("Biên LN gộp", pct(r["gross_margin"]))
        c[3].metric("Biên LN ròng", pct(r["net_margin"]))
        c[4].metric("ROE (năm hóa)", pct(r["roe_annualized"]))
        missing = [n for n, k in (("LNST", "npat"), ("ROE", "roe_annualized")) if pd.isna(r[k])]
        if missing:
            st.caption(f"'–': {', '.join(missing)} của quý này chưa vào Gold (bị chặn DQ hoặc thiếu kỳ đầu), xem trang Độ phủ.")
    if len(yd):
        r = yd.iloc[0]
        st.subheader(f"Năm gần nhất có số: FY{r['fiscal_year']}")
        c = st.columns(5)
        c[0].metric("Doanh thu thuần", tỷ(r["net_revenue"]), None if pd.isna(r["revenue_yoy"]) else f"{pct(r['revenue_yoy'])} YoY")
        c[1].metric("LN sau thuế", tỷ(r["npat"]), None if pd.isna(r["npat_yoy"]) else f"{pct(r['npat_yoy'])} YoY")
        c[2].metric("LNST cổ đông công ty mẹ", tỷ(r["npat_parent"]))
        c[3].metric("ROE", pct(r["roe"]))
        c[4].metric("ROA", pct(r["roa"]))
    cov = q("SELECT status, count(*) AS n FROM v_coverage GROUP BY 1")
    st.caption("Độ phủ ô (kỳ × chỉ tiêu chính): " + ", ".join(f"{s} {n}" for s, n in cov.itertuples(index=False))
               + ". Nguồn: BCTC hợp nhất do MWG công bố trên mwg.vn; chỉ số liệu qua kiểm tra DQ mới vào Gold.")


def _grain() -> tuple[str, pd.DataFrame]:
    grain = st.radio("Kỳ", ["Quý", "Năm"], horizontal=True, key="grain")
    return grain, quarter_frame() if grain == "Quý" else year_frame()


def page_results():
    st.header("Kết quả kinh doanh")
    grain, df = _grain()
    for title, col, color in (("Doanh thu thuần (tỷ đồng)", "net_revenue", COLORS["revenue"]),
                              ("Lợi nhuận sau thuế (tỷ đồng)", "npat", COLORS["npat"])):
        fig = go.Figure(go.Bar(x=df["label"], y=df[col] / BILLION, marker_color=color, name=title,
                               hovertemplate="%{x}: %{y:,.0f} tỷ<extra></extra>"))
        fig.update_layout(title=title, showlegend=False, hovermode="closest")
        if grain == "Năm" and col == "npat":
            fig.add_trace(go.Bar(x=df["label"], y=df["npat_parent"] / BILLION, marker_color=COLORS["roa"],
                                 name="LNST cổ đông công ty mẹ", hovertemplate="%{x}: %{y:,.0f} tỷ<extra></extra>"))
            fig.update_layout(showlegend=True, barmode="group")
        st.plotly_chart(layout(fig, ",.0f"), use_container_width=True)
    st.caption("Ô trống = kỳ chưa vào Gold (bị chặn DQ hoặc không có tài liệu gốc), không nội suy. Quý 2/3/4 có thể là "
               "số suy ra (H1 − Q1, 9T − H1, năm − 9T) từ hai ô đã qua DQ.")
    with st.expander("Bảng số liệu"):
        st.dataframe(df[["label", "net_revenue", "gross_profit", "npat", "npat_parent"]].assign(
            **{c: df[c] / BILLION for c in ("net_revenue", "gross_profit", "npat", "npat_parent")}).round(0),
            hide_index=True, use_container_width=True)


def page_margins():
    st.header("Biên lợi nhuận và hiệu quả")
    grain, df = _grain()
    roe, roa = ("roe_annualized", "roa_annualized") if grain == "Quý" else ("roe", "roa")
    for title, series in (("Biên lợi nhuận", (("Biên LN gộp", "gross_margin", COLORS["gross"]),
                                              ("Biên LN ròng", "net_margin", COLORS["net"]))),
                          ("ROE / ROA" + (" (năm hóa ×4)" if grain == "Quý" else ""),
                           (("ROE", roe, COLORS["roe"]), ("ROA", roa, COLORS["roa"])))):
        fig = go.Figure([go.Scatter(x=df["label"], y=df[c], name=n, mode="lines+markers", connectgaps=False,
                                    line=dict(color=col, width=2), marker=dict(size=8),
                                    hovertemplate=n + ": %{y:.1%}<extra></extra>") for n, c, col in series])
        fig.update_layout(title=title)
        st.plotly_chart(layout(fig, ".0%"), use_container_width=True)
    st.caption("ROE/ROA dùng bình quân đầu kỳ và cuối kỳ; thiếu một đầu thì để trống. Đường đứt quãng = kỳ thiếu số.")


def page_structure():
    st.header("Cơ cấu tài sản – nguồn vốn")
    grain = st.radio("Kỳ", ["Cuối năm", "Cuối quý"], horizontal=True)
    df = q("SELECT * FROM v_balance_structure" + (" WHERE fiscal_quarter = 4" if grain == "Cuối năm" else "")
           + " ORDER BY period_end")
    if df.empty:
        st.info("Chưa có số B01 trong Gold.")
        return
    df["label"] = df.apply(lambda r: f"{r.fiscal_year}" if grain == "Cuối năm" else f"Q{r.fiscal_quarter}/{r.fiscal_year}", axis=1)
    names = {"110": "Tiền", "120": "Đầu tư TC ngắn hạn", "130": "Phải thu ngắn hạn", "140": "Hàng tồn kho",
             "150": "TS ngắn hạn khác", "200": "TS dài hạn", "300": "Nợ phải trả", "400": "Vốn chủ sở hữu"}
    order = list(dict.fromkeys(df.sort_values("period_end")["label"]))  # trục category theo thời gian
    x_tt99 = df.loc[df["regime"] == "TT99", "label"].min() if (df["regime"] == "TT99").any() else None
    for title, codes in (("Cơ cấu tài sản (% tổng tài sản)", ["110", "120", "130", "140", "150", "200"]),
                         ("Cơ cấu nguồn vốn (% tổng nguồn vốn)", ["300", "400"])):
        fig = go.Figure()
        for i, code in enumerate(codes):
            s = df[df["account_code"] == code]
            fig.add_trace(go.Bar(x=s["label"], y=s["share_of_total_assets"], name=names[code],
                                 marker=dict(color=STACK[i], line=dict(color="rgba(255,255,255,0.9)", width=1)),
                                 customdata=s["amount_vnd"] / BILLION,
                                 hovertemplate=names[code] + ": %{y:.1%} (%{customdata:,.0f} tỷ)<extra></extra>"))
        fig.update_layout(title=title, barmode="stack")
        broken = df[df["account_code"].isin(codes) & df["semantic_break"]]
        if x_tt99 is not None and len(broken):
            tt99_marker(fig, x_tt99)
        layout(fig, ".0%", 420).update_xaxes(categoryorder="array", categoryarray=order)
        st.plotly_chart(fig, use_container_width=True)
    if x_tt99 is not None:
        st.caption(TT99_NOTE + f" (kỳ đầu theo TT99 trong Gold: {x_tt99}). Tỷ trọng mỗi kỳ vẫn đúng theo chuẩn của kỳ đó.")
    st.caption("Kỳ thiếu một vài chỉ tiêu (bị chặn DQ) có cột không cộng đủ 100%.")


def page_coverage():
    st.header("Độ phủ dữ liệu")
    df = q("SELECT * FROM v_coverage ORDER BY fiscal_year, fiscal_quarter")
    kinds = {"quarter": "Theo quý", "year": "Theo năm", "instant": "Bảng cân đối (cuối quý)"}
    kind = st.radio("Nhóm", list(kinds), format_func=kinds.get, horizontal=True)
    d = df[df["period_kind"] == kind]
    labels = list(dict.fromkeys(d["period_label"]))
    inds = list(dict.fromkeys(d["indicator"]))
    code = {s: i for i, s in enumerate(STATUS_COLORS)}
    z = d.pivot(index="indicator", columns="period_label", values="status").reindex(index=inds, columns=labels)
    colorscale = [[i / 2, c] for i, c in enumerate(STATUS_COLORS.values())]
    fig = go.Figure(go.Heatmap(z=z.apply(lambda col: col.map(code)).values, x=labels, y=inds, text=z.values,
                               colorscale=colorscale, zmin=0, zmax=2, showscale=False, xgap=2, ygap=2,
                               hovertemplate="%{y} – %{x}: %{text}<extra></extra>"))
    st.plotly_chart(layout(fig, height=120 + 36 * len(inds)).update_layout(hovermode="closest"), use_container_width=True)
    st.markdown(" · ".join(f"<span style='color:{c}'>■</span> {s}" for s, c in STATUS_COLORS.items()),
                unsafe_allow_html=True)
    summary = d.groupby(["indicator", "status"]).size().unstack(fill_value=0).reindex(index=inds)
    st.dataframe(summary, use_container_width=True)
    st.caption("'bị chặn DQ': có số ở Silver nhưng chưa qua kiểm tra phương trình/critical (hoặc ô suy ra từ ô chưa qua). "
               "'không có tài liệu gốc': không có BCTC (hoặc cột so sánh) nào cho kỳ đó, xem docs/limitations.md.")


def page_lineage():
    st.header("Lineage: một số đến từ đâu")
    facts = q("""SELECT fiscal_year, fiscal_quarter, period_kind, statement, account_code, coalesce(name_vi, '') AS name_vi
                 FROM v_lineage ORDER BY fiscal_year DESC, fiscal_quarter DESC""")
    c = st.columns(3)
    year = c[0].selectbox("Năm", sorted(facts["fiscal_year"].unique(), reverse=True))
    f1 = facts[facts["fiscal_year"] == year]
    kind_label = lambda k: {"instant": "B01 cuối quý", "quarter": "B02 quý", "ytd": "B02 lũy kế", "year": "B02 năm"}[k]  # noqa: E731
    pk = c[1].selectbox("Loại kỳ", sorted(f1["period_kind"].unique()), format_func=kind_label)
    f2 = f1[f1["period_kind"] == pk]
    quarter = c[2].selectbox("Quý (0 = năm)", sorted(f2["fiscal_quarter"].unique()))
    f3 = f2[f2["fiscal_quarter"] == quarter].drop_duplicates(["statement", "account_code"])
    opts = [f"{r.statement} {r.account_code} – {r.name_vi}" for r in f3.itertuples()]
    default = next((i for i, o in enumerate(opts) if o.startswith(("B02 10 ", "B01 270 "))), 0)
    pick = st.selectbox("Chỉ tiêu", opts, index=default)
    st_, code = pick.split(" – ")[0].split(" ")
    r = q(f"""SELECT * FROM v_lineage WHERE fiscal_year = {int(year)} AND fiscal_quarter = {int(quarter)}
              AND period_kind = '{pk}' AND statement = '{st_}' AND account_code = '{code}'""").iloc[0]
    c = st.columns(3)
    c[0].metric("Giá trị", tỷ(r["amount_vnd"]), help=f"{int(r['amount_vnd']):,} VND".replace(",", "."))
    c[1].metric("Nguồn gốc giá trị (value_origin)", r["value_origin"])
    c[2].metric("Trang PDF", "–" if pd.isna(r["page"]) else int(r["page"]))
    info = {"Tài liệu": r["source_title"], "File Bronze": r["source_path"], "URL gốc": r["source_url"],
            "Ngày đăng": r["source_published"], "Kỳ của tài liệu": r["source_period"], "Ngôn ngữ": r["source_lang"],
            "Mức đảm bảo (source_priority)": r["source_priority"], "Chế độ kế toán": r["regime"],
            "Số so sánh (cột kỳ trước)": bool(r["is_comparative"]), "Số suy ra": bool(r["is_derived"]),
            "Suy ra từ": r["derived_from"] or "–", "Nhập tay (override)": bool(r["is_manual"]),
            "sha256": r["source_doc_sha256"]}
    st.table(pd.DataFrame({"": list(info), "Giá trị": [str(v) for v in info.values()]}).set_index(""))
    path = ROOT / str(r["source_path"])
    if pd.notna(r["page"]) and path.exists() and path.suffix.lower() == ".pdf":
        if st.toggle("Xem trang PDF gốc"):
            import pypdfium2 as pdfium
            pdf = pdfium.PdfDocument(str(path))
            st.image(pdf[int(r["page"]) - 1].render(scale=1.4).to_pil(),
                     caption=f"{path.name}, trang {int(r['page'])}", use_container_width=True)
    elif pd.notna(r["page"]):
        st.caption("File PDF gốc không có trên máy này (Bronze không đưa lên git); mở URL gốc để xem.")
    else:
        st.caption("Không có số trang: số suy ra từ hai kỳ khác, hoặc ô được điền bằng bỏ phiếu giữa các lần in lại.")


PAGES = {"Tổng quan": page_overview, "Kết quả kinh doanh": page_results, "Biên lợi nhuận": page_margins,
         "Cơ cấu tài sản – nguồn vốn": page_structure, "Độ phủ dữ liệu": page_coverage, "Lineage": page_lineage}


def main():
    st.sidebar.title("MWG Financial Lakehouse")
    if not DB.exists():
        st.error("Chưa có data/gold/mwg.duckdb: chạy `python run_pipeline.py` trước.")
        st.stop()
    page = st.sidebar.radio("Trang", list(PAGES), key="page")
    st.sidebar.caption("BCTC hợp nhất CTCP Đầu tư Thế Giới Di Động (HOSE: MWG). Tiền: tỷ đồng. "
                       "Dữ liệu: Bronze → Silver → Gold (Delta Lake), truy vấn DuckDB read-only.")
    PAGES[page]()


if __name__ == "__main__" or "streamlit" in sys.modules:
    main()
