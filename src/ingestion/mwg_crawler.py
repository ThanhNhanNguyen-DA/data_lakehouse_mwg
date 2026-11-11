"""Crawler tài liệu Quan hệ nhà đầu tư của MWG -> tầng Bronze.

Danh sách tài liệu lấy từ JSON API mà trang mwg.vn/bao-cao gọi (xem config/sources.yaml).
Incremental bằng manifest (data/_state/manifest.parquet, khóa là URL file).

    python -m src.ingestion.mwg_crawler              # incremental: chỉ các năm mới nhất
    python -m src.ingestion.mwg_crawler --backfill   # lần đầu: toàn bộ lịch sử
    python -m src.ingestion.mwg_crawler --dry-run    # chỉ liệt kê + phân loại, không tải PDF
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import polars as pl
import requests
import yaml

from src.common.paths import BRONZE_DIR, CONFIG_DIR, ROOT, STATE_DIR
from src.common.text import norm
from src.ingestion.http_utils import get

SOURCES_PATH = CONFIG_DIR / "sources.yaml"
MANIFEST_PATH = STATE_DIR / "manifest.parquet"
ERRORS_PATH = STATE_DIR / "download_errors.csv"
BRONZE_ROOT = BRONZE_DIR / "source=mwg_ir"
DEAD_LINK_STATUS = {404, 410}  # link chết trên nguồn: ghi lại rồi bỏ qua, không dừng cả lần chạy
ERROR_FIELDS = ["url", "title", "doc_type", "period", "http_status", "first_seen", "last_seen"]
_PLACEHOLDER = re.compile(r"<[^>]*>")

MANIFEST_SCHEMA = {
    "url": pl.Utf8, "post_id": pl.Int64, "title": pl.Utf8, "doc_type": pl.Utf8,
    "period": pl.Utf8, "published": pl.Utf8, "category_year": pl.Int32,
    "assurance_level": pl.Int8, "sha256": pl.Utf8, "size": pl.Int64,
    "http_status": pl.Int16, "is_restatement": pl.Boolean, "path": pl.Utf8,
    "fetched_at": pl.Utf8,
}


@lru_cache(maxsize=None)
def load_config(path: Path = SOURCES_PATH) -> dict:
    with Path(path).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def unfilled_fields(cfg: dict) -> list[str]:
    """Các trường trong 'listing' còn để trống hoặc placeholder <...>."""
    listing = cfg.get("listing")
    if not listing:
        return ["listing"]
    return [f"listing.{k}" for k, v in listing.items()
            if v is None or (isinstance(v, str) and _PLACEHOLDER.search(v))]


# ---------- Nhận diện loại tài liệu ----------

def _has_word(text: str, keyword: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", text) is not None


def classify(title: str, rules: list[dict] | None = None) -> str | None:
    """Trả về doc_type theo quy tắc đầu tiên khớp trong doc_rules, hoặc None."""
    if rules is None:
        rules = load_config()["doc_rules"]
    t = norm(title)
    for rule in rules:
        all_kw, any_kw, none_kw = rule.get("all", []), rule.get("any", []), rule.get("none", [])
        if not all_kw and not any_kw:
            raise ValueError(f"Quy tắc thiếu từ khóa all/any: {rule}")
        if (all(_has_word(t, k) for k in all_kw)
                and (not any_kw or any(_has_word(t, k) for k in any_kw))
                and not any(_has_word(t, k) for k in none_kw)):
            return rule["doc_type"]
    return None


def assurance_level(title: str, doc_type: str, cfg: dict | None = None) -> int:
    """Mức tin cậy: kiểm toán 3 > soát xét 2 > tự lập 1 (max của mặc định và từ khóa)."""
    a = (cfg or load_config())["assurance"]
    t = norm(title)
    found = [lvl for kw, lvl in a["keywords"].items() if _has_word(t, kw)]
    return max([a["default"].get(doc_type, 1), *found])


# ---------- Nhận diện kỳ báo cáo ----------

_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4}
_YEAR = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
_QUARTER = re.compile(r"\b(?:quy|quarter|q)\s*([1-4]|iv|iii|ii|i)\b")
_MONTH = re.compile(r"\b(1[0-2]|[1-9])\s*(?:thang|months?)\b|\bthang\s*(1[0-2]|[1-9])\b")
_MONTH_RANGE = re.compile(r"\bthang\s*(1[0-2]|[1-9])\s*\+\s*(1[0-2]|[1-9])\b")  # "tháng 1+2"
_HALF_HINTS = ("soat xet", "ban nien", "giua nien do", "6 thang", "sau thang",
               "semi-annual", "half-year", "half year", "6 months", "six months")


def parse_period(title: str, doc_type: str | None = None,
                 default_year: int | None = None) -> str | None:
    """Kỳ báo cáo từ tiêu đề: '2026-Q3', '2026-H1', '2026' (năm) hoặc '2026-M10' (báo cáo tháng).

    default_year: năm dùng khi tiêu đề không ghi năm (crawler truyền năm của danh mục trên mwg.vn).
    Trả về None nếu không xác định được, để crawler ghi 'unknown' thay vì đoán.
    """
    t = norm(title)
    y = _YEAR.search(t)
    if y:
        year = y.group(1)
    elif default_year:
        year = str(default_year)
    else:
        return None

    if doc_type == "FS_CONSOLIDATED_Y":
        return year
    if doc_type == "FS_CONSOLIDATED_H":
        return f"{year}-H1"
    if doc_type == "MONTHLY_RESULT":
        if _has_word(t, "ca nam"):
            return f"{year}-M12"
        rng = _MONTH_RANGE.search(t)
        if rng:  # báo cáo gộp nhiều tháng: lấy tháng cuối (số lũy kế tới tháng đó)
            return f"{year}-M{int(rng.group(2)):02d}"
        m = _MONTH.search(t)
        return f"{year}-M{int(m.group(1) or m.group(2)):02d}" if m else None

    q = _QUARTER.search(t)
    if q:
        val = q.group(1)
        return f"{year}-Q{_ROMAN.get(val) or int(val)}"
    if doc_type == "FS_CONSOLIDATED_Q":
        return None  # loại quý mà không thấy số quý: không đoán
    if any(h in t for h in _HALF_HINTS):
        return f"{year}-H1"
    return year


# ---------- Liệt kê tài liệu qua JSON API ----------

def _api(endpoint: str, params: dict, cfg: dict) -> list[dict]:
    """Gọi API danh sách, tự lật trang theo totalRecord. Chỉ trả bản ghi đang hiển thị."""
    lst = cfg["listing"]
    out: list[dict] = []
    for page_index in range(1, 21):
        query = {"langID": lst["lang_id"], **params,
                 "pageIndex": page_index, "pageSize": lst["page_size"]}
        url = f"{lst['api_base'].rstrip('/')}{endpoint}?{urlencode(query)}"
        body = get(url, referer=lst["referer"]).json()
        if body.get("code") != 0:
            raise RuntimeError(f"[crawl] API báo lỗi {body.get('code')}: {body.get('message')} ({url})")
        data = body.get("data") or []
        out.extend(data)
        total = data[0].get("totalRecord", len(data)) if data else 0
        if not data or len(out) >= total:
            break
    return [d for d in out if d.get("isActived") and not d.get("isDeleted")]


def list_year_sections(cfg: dict) -> list[dict]:
    """[{year, section, category_id}] theo năm mới nhất trước, chỉ các section trong config."""
    lst = cfg["listing"]
    wanted = {norm(s): s for s in lst["sections"]}
    years = _api(lst["category_endpoint"], {"parentID": lst["root_category_id"]}, cfg)
    out = []
    for y in years:
        if not str(y.get("categoryName", "")).strip().isdigit():
            continue
        ids = [i for i in str(y.get("childrenIDList") or "").split(",") if i.strip()]
        names = [n for n in str(y.get("childrenNameList") or "").split(";") if n.strip()]
        if len(ids) != len(names):
            raise RuntimeError(f"[crawl] Danh mục năm {y['categoryName']} có số ID và tên nhóm con lệch nhau")
        for cid, name in zip(ids, names):
            if norm(name) in wanted:
                out.append({"year": int(y["categoryName"]), "section": wanted[norm(name)],
                            "category_id": int(cid)})
    return sorted(out, key=lambda r: (-r["year"], lst["sections"].index(r["section"])))


def list_documents(cfg: dict, max_years: int | None):
    """Sinh từng tài liệu (đã lọc bài ẩn/xóa) của max_years năm mới nhất (None = tất cả)."""
    sections = list_year_sections(cfg)
    years = sorted({s["year"] for s in sections}, reverse=True)
    keep = set(years[:max_years] if max_years else years)
    for s in sections:
        if s["year"] not in keep:
            continue
        for p in _api(cfg["listing"]["post_endpoint"],
                      {"categoryID": s["category_id"], "condition": cfg["listing"]["condition"]}, cfg):
            yield {
                "url": (p.get("fileLink") or "").strip(),
                "post_id": p.get("postID"),
                "title": " ".join(str(p.get("title") or "").split()),
                "published": str(p.get("publishDate") or "")[:10] or None,
                "category_year": s["year"],
                "section": s["section"],
            }


# ---------- Manifest + Bronze ----------

def load_manifest() -> pl.DataFrame:
    if MANIFEST_PATH.exists():
        return pl.read_parquet(MANIFEST_PATH)
    return pl.DataFrame(schema=MANIFEST_SCHEMA)


def _save_manifest(df: pl.DataFrame) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST_PATH.with_suffix(".tmp")
    df.write_parquet(tmp)
    tmp.replace(MANIFEST_PATH)


def _rel(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _log_download_error(doc: dict, doc_type: str, period: str, status: int) -> None:
    """Ghi link tải lỗi vào download_errors.csv (một dòng mỗi URL, cập nhật last_seen).

    Link lỗi không vào manifest, nên lần backfill sau vẫn thử lại phòng khi nguồn sửa link.
    """
    rows: dict[str, dict] = {}
    if ERRORS_PATH.exists():
        with ERRORS_PATH.open(encoding="utf-8", newline="") as f:
            rows = {r["url"]: r for r in csv.DictReader(f)}
    today = date.today().isoformat()
    prev = rows.get(doc["url"], {})
    rows[doc["url"]] = {"url": doc["url"], "title": doc["title"], "doc_type": doc_type,
                        "period": period, "http_status": status,
                        "first_seen": prev.get("first_seen", today), "last_seen": today}
    ERRORS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ERRORS_PATH.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ERROR_FIELDS)
        w.writeheader()
        w.writerows(rows.values())


def _is_pdf_url(url: str) -> bool:
    return urlsplit(url).path.lower().endswith(".pdf")


def crawl(backfill: bool = False, dry_run: bool = False) -> list[dict]:
    cfg = load_config()
    missing = unfilled_fields(cfg)
    if missing:
        raise SystemExit(f"[crawl] sources.yaml thiếu: {', '.join(missing)}")

    mf = load_manifest()
    known = set(mf["url"].to_list())
    seen_periods = {(r["doc_type"], r["period"]): r["sha256"] for r in mf.iter_rows(named=True)}
    max_years = None if backfill else cfg["incremental_years"]
    new_rows: list[dict] = []
    skipped = failed = 0

    for doc in list_documents(cfg, max_years):
        doc_type = classify(doc["title"])
        if doc_type is None:
            skipped += 1
            continue
        if not doc["url"] or doc["url"] == "#":
            print(f"[crawl] Chưa có file (sắp công bố {doc['published']}): {doc['title']}")
            continue
        if not _is_pdf_url(doc["url"]):
            print(f"[crawl] Bỏ qua file không phải PDF: {doc['title']} -> {doc['url'][-40:]}")
            continue

        period = parse_period(doc["title"], doc_type, default_year=doc["category_year"])
        if period and period[:4] != str(doc["category_year"]):
            print(f"[crawl] Lưu ý: kỳ {period} theo tiêu đề khác năm danh mục {doc['category_year']}: "
                  f"{doc['title']}")
        period = period or "unknown"

        if doc["url"] in known:
            continue
        if dry_run:
            print(f"[crawl] (dry-run) {doc_type:<18} {period:<8} {doc['published']}  {doc['title']}")
            new_rows.append({**doc, "doc_type": doc_type, "period": period})
            continue

        try:
            r = get(doc["url"], referer=cfg["listing"]["referer"], kind="pdf")
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status not in DEAD_LINK_STATUS:
                raise
            print(f"[crawl] Link chết (HTTP {status}), bỏ qua: {doc['title']} -> {doc['url']}")
            _log_download_error(doc, doc_type, period, status)
            failed += 1
            continue
        content = r.content
        if not content.startswith(b"%PDF"):
            print(f"[crawl] Nội dung tải về không phải PDF, bỏ qua: {doc['url']}")
            continue

        sha = hashlib.sha256(content).hexdigest()
        out_dir = BRONZE_ROOT / f"doc_type={doc_type}" / f"ingestion_date={date.today().isoformat()}"
        out_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = out_dir / f"{cfg['symbol']}_{period.replace('-', '')}_{sha[:8]}.pdf"
        if not pdf_path.exists():  # Bronze append-only: không ghi đè
            pdf_path.write_bytes(content)

        prev_sha = seen_periods.get((doc_type, period))
        meta = {
            "url": doc["url"], "post_id": doc["post_id"], "title": doc["title"],
            "doc_type": doc_type, "period": period, "published": doc["published"],
            "category_year": doc["category_year"],
            "assurance_level": assurance_level(doc["title"], doc_type, cfg),
            "sha256": sha, "size": len(content), "http_status": r.status_code,
            "is_restatement": prev_sha is not None and prev_sha != sha,
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
        }
        meta_path = pdf_path.with_name(pdf_path.name + ".meta.json")
        if not meta_path.exists():
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        row = {**meta, "path": _rel(pdf_path)}
        mf = pl.concat([mf, pl.DataFrame([row], schema=MANIFEST_SCHEMA)])
        _save_manifest(mf)  # lưu sau mỗi file để dừng giữa chừng không mất trạng thái
        known.add(doc["url"])
        seen_periods[(doc_type, period)] = sha
        new_rows.append(row)
        print(f"[bronze] {doc_type} {period} <- {doc['title']}")

    print(f"[crawl] Bỏ qua {skipped} tài liệu không thuộc loại cần lấy.")
    if failed:
        print(f"[crawl] Link chết: {failed} (xem {_rel(ERRORS_PATH)}).")
    if new_rows:
        print(f"[crawl] Tài liệu mới: {len(new_rows)}" + (" (dry-run, chưa tải)" if dry_run else ""))
    else:
        print("[crawl] Không có tài liệu mới.")
    return new_rows


def main(argv: list[str]) -> int:
    crawl(backfill="--backfill" in argv, dry_run="--dry-run" in argv)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
