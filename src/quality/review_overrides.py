"""Trang HTML để người dùng duyệt các ô gợi ý sửa (status = pending) trong config/manual_overrides.csv.

Mỗi ô: ảnh crop cả dòng trên trang PDF gốc (đã xoay đúng chiều), giá trị OCR, giá trị gợi ý, loại lỗi nghi ngờ.
Người dùng tự đọc ảnh rồi đổi status thành confirmed (hoặc sửa amount_vnd) trong CSV; trang này không ghi gì.

    python -m src.quality.review_overrides      # -> review/overrides_review.html
"""
from __future__ import annotations

import base64
import csv
import html
import io

import polars as pl

from src.common.paths import CONFIG_DIR, ROOT, SILVER_DIR
from src.extraction.ocr import PASS2_DPI, _upright
from src.ingestion.mwg_crawler import load_manifest

OUT = ROOT / "review" / "overrides_review.html"
MARGIN = 40  # px ở 300 dpi: lấy thêm trên/dưới dòng để thấy dòng kề


def error_kind(ocr_value: str, suggested: str) -> str:
    """Mô tả khác biệt kiểu lỗi OCR: nhầm chữ số ở vị trí nào, hay chỉ mất dấu âm."""
    if not ocr_value:
        return "ô trống / OCR không đọc được"
    a, b = int(ocr_value), int(suggested)
    if abs(a) == abs(b):
        return "mất dấu âm / ngoặc" if a != b else "giống nhau"
    sa, sb = str(abs(a)), str(abs(b))
    if len(sa) != len(sb):
        return f"khác số chữ số ({len(sa)} → {len(sb)})"
    diffs = [f"'{x}'→'{y}' ở chữ số thứ {i + 1}" for i, (x, y) in enumerate(zip(sa, sb)) if x != y]
    return "nhầm chữ số: " + ", ".join(diffs)


def _fmt(v: str) -> str:
    return f"{int(v):,}".replace(",", ".") if v else "–"


def _crop(path, sha: str, page: int, y0: float, y1: float) -> str:
    img = _upright(path, sha, page, PASS2_DPI)  # row_y0/row_y1 đo trên ảnh 300 dpi đã xoay
    box = (0, max(0, int(y0) - MARGIN), img.width, min(img.height, int(y1) + MARGIN))
    buf = io.BytesIO()
    img.crop(box).convert("L").save(buf, format="PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


def build() -> int:
    with (CONFIG_DIR / "manual_overrides.csv").open(encoding="utf-8", newline="") as f:
        pending = [r for r in csv.DictReader(f) if r.get("status") == "pending"]
    raw = pl.read_delta(str(SILVER_DIR / "fs_extracted_raw"))
    paths = dict(zip(load_manifest()["sha256"], load_manifest()["path"]))
    items = []
    for r in pending:
        hit = raw.filter((pl.col("source_doc_sha256") == r["source_doc_sha256"]) & (pl.col("statement") == r["statement"])
                         & (pl.col("account_code") == r["account_code"]) & (pl.col("period_kind") == r["period_kind"]))
        img, note = "", ""
        cell = hit.row(0, named=True) if hit.height else None
        if cell and cell["page"] and cell["row_y0"] is not None and r["source_doc_sha256"] in paths:
            img = _crop(ROOT / paths[r["source_doc_sha256"]], r["source_doc_sha256"], int(cell["page"]),
                        cell["row_y0"], cell["row_y1"])
        else:
            note = "không có tọa độ dòng (ô do bỏ phiếu điền hoặc chưa chạy lại bóc tách): mở PDF gốc ở trang ghi trong CSV"
        items.append((r, cell, img, note))

    css = ("body{font-family:system-ui,sans-serif;margin:16px;background:#fff;color:#111}"
           "section{border:1px solid #ccc;border-radius:6px;padding:12px;margin:0 0 16px}"
           "img{max-width:100%;border:1px solid #ddd}table{border-collapse:collapse}"
           "td{padding:2px 10px 2px 0;vertical-align:top}.k{color:#555}.n{font-family:monospace;font-size:1.1em}")
    out = ["<!doctype html><html lang='vi'><head><meta charset='utf-8'>"
           "<meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>Duyệt ô gợi ý sửa</title><style>{css}</style></head><body>",
           f"<h1>Ô chờ xác nhận ({len(items)})</h1>",
           "<p>Đọc số trên ảnh. Đúng với giá trị gợi ý thì đổi <code>status</code> thành <code>confirmed</code> "
           "trong <code>config/manual_overrides.csv</code>; sai thì sửa <code>amount_vnd</code> theo ảnh rồi để "
           "<code>status</code> trống (nhập tay) hoặc xóa dòng.</p>"]
    for i, (r, cell, img, note) in enumerate(items, 1):
        fields = [("Kỳ", f"{r['fiscal_year']} {r['fiscal_quarter']} · {r['statement']} · {r['period_kind']}"),
                  ("Mã số", r["account_code"] + (f" – {cell['item_name']}" if cell and cell["item_name"] else "")),
                  ("Giá trị OCR", f"<span class='n'>{_fmt(r['ocr_value'])}</span>"),
                  ("Giá trị gợi ý", f"<span class='n'><b>{_fmt(r['amount_vnd'])}</b></span>"),
                  ("Loại lỗi nghi ngờ", error_kind(r["ocr_value"], r["amount_vnd"])),
                  ("Lý do", r["reason"]),
                  ("Tài liệu", f"{r['source_doc_sha256'][:12]}… trang {r['page']}"
                               + (f" · {html.escape(cell['url'])}" if cell else ""))]
        out.append(f"<section><h2>{i}. {r['statement']} {r['account_code']}</h2><table>"
                   + "".join(f"<tr><td class='k'>{k}</td><td>{v if k in ('Giá trị OCR', 'Giá trị gợi ý') else html.escape(v)}</td></tr>"
                             for k, v in fields) + "</table>"
                   + (f"<p><img alt='Dòng {r['account_code']} trên trang PDF' src='data:image/png;base64,{img}'></p>" if img
                      else f"<p><i>{html.escape(note)}</i></p>") + "</section>")
    out.append("</body></html>")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(out), encoding="utf-8")
    print(f"[DQ] {OUT.relative_to(ROOT)}: {len(items)} ô chờ xác nhận, {sum(1 for x in items if x[2])} có ảnh")
    return len(items)


if __name__ == "__main__":
    build()
