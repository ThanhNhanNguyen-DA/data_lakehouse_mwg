"""Crawler tài liệu Quan hệ nhà đầu tư của MWG -> tầng Bronze.

Danh sách tài liệu lấy từ JSON API mà trang mwg.vn/bao-cao gọi (xem config/sources.yaml).
Incremental bằng manifest (data/_state/manifest.parquet, khóa là URL file).

Nguồn chính là bản vi-VN. Bản en-US chỉ được tải khi kỳ đó (doc_type, period) chưa có bản
vi-VN tải được (link vi-VN chết hoặc không có), hoặc khi kỳ đó nằm trong listing.cross_check.

    python -m src.ingestion.mwg_crawler              # incremental: chỉ các năm mới nhất
    python -m src.ingestion.mwg_crawler --backfill   # lần đầu: toàn bộ lịch sử
    python -m src.ingestion.mwg_crawler --dry-run    # chỉ liệt kê + phân loại, không tải
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
from src.extraction.pdf_extractor import has_text_layer
from src.ingestion.http_utils import RobotsDisallowed, get

SOURCES_PATH = CONFIG_DIR / "sources.yaml"
MANIFEST_PATH = STATE_DIR / "manifest.parquet"
ERRORS_PATH = STATE_DIR / "download_errors.csv"
BRONZE_ROOT = BRONZE_DIR / "source=mwg_ir"
DEAD_LINK_STATUS = {404, 410}  # link chết trên nguồn: ghi lại rồi bỏ qua, không dừng cả lần chạy

# Định dạng được lưu vào Bronze. Đuôi URL quyết định có tải hay không,
# còn file_type lấy theo magic bytes của nội dung (đuôi URL có thể sai).
DOWNLOAD_EXTS = {".pdf", ".jpg", ".jpeg", ".png"}
MAGIC = {"pdf": b"%PDF", "jpg": b"\xff\xd8\xff", "png": b"\x89PNG\r\n\x1a\n"}
ERROR_FIELDS = ["url", "lang", "title", "doc_type", "period", "http_status", "first_seen", "last_seen"]
ANOMALIES_PATH = STATE_DIR / "link_anomalies.csv"
ANOMALY_FIELDS = ["title", "period_from_title", "url", "period_in_manifest", "detected_at"]
_PLACEHOLDER = re.compile(r"<[^>]*>")
API_CODE_NO_DATA = 24000  # API trả code này kèm "Không có dữ liệu" khi danh mục rỗng

MANIFEST_SCHEMA = {
    "url": pl.Utf8, "post_id": pl.Int64, "lang": pl.Utf8, "fetch_reason": pl.Utf8,
    "title": pl.Utf8, "doc_type": pl.Utf8, "file_type": pl.Utf8, "has_text_layer": pl.Boolean,
    "period": pl.Utf8, "published": pl.Utf8, "category_year": pl.Int32,
    "assurance_level": pl.Int8, "sha256": pl.Utf8, "size": pl.Int64,
    "http_status": pl.Int16, "is_restatement": pl.Boolean, "path": pl.Utf8,
    "fetched_at": pl.Utf8, "period_ambiguous": pl.Boolean, "period_candidates": pl.Utf8,
}
# Giá trị cho các cột chưa có ở manifest cũ (trước khi có nguồn en-US, chỉ có bản vi-VN PDF)
_MANIFEST_DEFAULTS = {"lang": "vi-VN", "fetch_reason": "primary", "file_type": "pdf",
                      "has_text_layer": None, "period_ambiguous": False, "period_candidates": None}
# Một URL được nguồn gắn cho nhiều kỳ: tải một lần với kỳ chờ xác định, gán kỳ thật sau khi
# đối chiếu ngày báo cáo trong PDF (resolve_period).
PENDING = "pending"
PENDING_DOC_TYPE = "PENDING"  # khi các kỳ ứng viên còn khác cả loại tài liệu (vd. 1H17 và 3Q17)
_REASON_RANK = {"primary": 0, "cross_check": 1, "fallback": 2}


@lru_cache(maxsize=None)
def load_config(path: Path = SOURCES_PATH) -> dict:
    with Path(path).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _placeholder_keys(prefix: str, d: dict) -> list[str]:
    return [f"{prefix}.{k}" for k, v in d.items()
            if v is None or (isinstance(v, str) and _PLACEHOLDER.search(v))]


def unfilled_fields(cfg: dict) -> list[str]:
    """Các trường trong 'listing' (kể cả từng ngôn ngữ) còn trống hoặc placeholder <...>."""
    listing = cfg.get("listing")
    if not listing:
        return ["listing"]
    out = _placeholder_keys("listing", listing)
    langs = listing.get("languages") or []
    if not langs:
        out.append("listing.languages")
    for i, lang in enumerate(langs):
        out += _placeholder_keys(f"listing.languages[{i}]", lang)
    return out


# ---------- Nhận diện loại tài liệu ----------

def _key(title: str) -> str:
    """Chuỗi để so khớp tiêu đề: norm() + coi '_' là dấu cách ("QIII_2016" -> "qiii 2016")."""
    return norm(title).replace("_", " ")


def _has_word(text: str, keyword: str) -> bool:
    """Khớp nguyên từ; từ khóa kết thúc bằng '*' khớp tiền tố của một từ."""
    if keyword.endswith("*"):
        return re.search(rf"(?<!\w){re.escape(keyword[:-1])}", text) is not None
    return re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", text) is not None


def classify(title: str, rules: list[dict] | None = None) -> str | None:
    """Trả về doc_type theo quy tắc đầu tiên khớp trong doc_rules, hoặc None."""
    if rules is None:
        rules = load_config()["doc_rules"]
    t = _key(title)
    for rule in rules:
        all_kw, any_kw, none_kw = rule.get("all", []), rule.get("any", []), rule.get("none", [])
        if not all_kw and not any_kw:
            raise ValueError(f"Quy tắc thiếu từ khóa all/any: {rule}")
        if (all(_has_word(t, k) for k in all_kw)
                and (not any_kw or any(_has_word(t, k) for k in any_kw))
                and not any(_has_word(t, k) for k in none_kw)):
            return rule["doc_type"]
    return None


def assurance_level(title: str, doc_type: str, cfg: dict | None = None,
                    lang: str = "vi-VN") -> int:
    """Mức tin cậy: kiểm toán 3 > soát xét 2 > tự lập 1 (max của mặc định và từ khóa theo ngôn ngữ)."""
    a = (cfg or load_config())["assurance"]
    t = _key(title)
    found = [lvl for kw, lvl in a["keywords"].get(lang, {}).items() if _has_word(t, kw)]
    return max([a["default"].get(doc_type, 1), *found])


# ---------- Nhận diện kỳ báo cáo ----------

_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4}
_YEAR = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
_QUARTER = re.compile(r"\b(?:quy|quarter|q)\s*([1-4]|iv|iii|ii|i)\b")
_EN_QUARTER_YEAR = re.compile(r"\b([1-4])q(\d{4}|\d{2})\b")   # "3q19", "1q2023"
_EN_HALF_YEAR = re.compile(r"\b1h(\d{4}|\d{2})\b")             # "1h18", "1h2020"
_EN_FISCAL_YEAR = re.compile(r"\bfy\s*(\d{4}|\d{2})\b")        # "fy2019", "fy 2017"
_MONTH = re.compile(r"\b(1[0-2]|[1-9])\s*(?:thang|months?)\b|\bthang\s*(1[0-2]|[1-9])\b")
_MONTH_RANGE = re.compile(r"\bthang\s*(1[0-2]|[1-9])\s*\+\s*(1[0-2]|[1-9])\b")  # "tháng 1+2"
_EN_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_EN_MONTH = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b")  # "ytd mar 2018"
_HALF_HINTS = ("soat xet", "ban nien", "giua nien do", "6 thang", "sau thang",
               "semi-annual", "half-year", "half year", "1st half", "6 months", "six months")


def _full_year(yy: str) -> str:
    return yy if len(yy) == 4 else f"20{yy}"


def _year_in(t: str) -> str | None:
    y = _YEAR.search(t)
    if y:
        return y.group(1)
    for rx, grp in ((_EN_QUARTER_YEAR, 2), (_EN_HALF_YEAR, 1), (_EN_FISCAL_YEAR, 1)):
        m = rx.search(t)
        if m:
            return _full_year(m.group(grp))
    return None


def parse_period(title: str, doc_type: str | None = None,
                 default_year: int | None = None) -> str | None:
    """Kỳ báo cáo từ tiêu đề: '2026-Q3', '2026-H1', '2026' (năm) hoặc '2026-M10' (báo cáo tháng).

    default_year: năm dùng khi tiêu đề không ghi năm (crawler truyền năm của danh mục trên mwg.vn).
    Trả về None nếu không xác định được, để crawler ghi 'unknown' thay vì đoán.
    """
    t = _key(title)
    year = _year_in(t) or (str(default_year) if default_year else None)
    if year is None:
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
        if m:
            return f"{year}-M{int(m.group(1) or m.group(2)):02d}"
        en = _EN_MONTH.search(t)
        return f"{year}-M{_EN_MONTHS.index(en.group(1)) + 1:02d}" if en else None

    q = _QUARTER.search(t)
    if q:
        val = q.group(1)
        return f"{year}-Q{_ROMAN.get(val) or int(val)}"
    qy = _EN_QUARTER_YEAR.search(t)
    if qy:
        return f"{year}-Q{qy.group(1)}"
    if doc_type == "FS_CONSOLIDATED_Q":
        return None  # loại quý mà không thấy số quý: không đoán
    if _EN_HALF_YEAR.search(t) or any(h in t for h in _HALF_HINTS):
        return f"{year}-H1"
    return year


# ---------- Liệt kê tài liệu qua JSON API ----------

def _api(endpoint: str, params: dict, cfg: dict, lang_cfg: dict) -> list[dict]:
    """Gọi API danh sách, tự lật trang theo totalRecord. Chỉ trả bản ghi đang hiển thị."""
    lst = cfg["listing"]
    out: list[dict] = []
    for page_index in range(1, 21):
        query = {"langID": lang_cfg["lang_id"], **params,
                 "pageIndex": page_index, "pageSize": lst["page_size"]}
        url = f"{lst['api_base'].rstrip('/')}{endpoint}?{urlencode(query)}"
        body = get(url, referer=lang_cfg["referer"]).json()
        if body.get("code") == API_CODE_NO_DATA:  # danh mục rỗng (vd. en-US năm 2015)
            break
        if body.get("code") != 0:
            raise RuntimeError(f"[crawl] API báo lỗi {body.get('code')}: {body.get('message')} ({url})")
        data = body.get("data") or []
        out.extend(data)
        total = data[0].get("totalRecord", len(data)) if data else 0
        if not data or len(out) >= total:
            break
    return [d for d in out if d.get("isActived") and not d.get("isDeleted")]


def list_year_sections(cfg: dict, lang_cfg: dict) -> list[dict]:
    """[{year, section, category_id}] theo năm mới nhất trước, chỉ các section trong config."""
    wanted = {norm(s): s for s in lang_cfg["sections"]}
    years = _api(cfg["listing"]["category_endpoint"],
                 {"parentID": lang_cfg["root_category_id"]}, cfg, lang_cfg)
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
    return sorted(out, key=lambda r: (-r["year"], lang_cfg["sections"].index(r["section"])))


def list_documents(cfg: dict, lang_cfg: dict, max_years: int | None):
    """Sinh từng tài liệu (đã lọc bài ẩn/xóa) của max_years năm mới nhất (None = tất cả)."""
    sections = list_year_sections(cfg, lang_cfg)
    years = sorted({s["year"] for s in sections}, reverse=True)
    keep = set(years[:max_years] if max_years else years)
    for s in sections:
        if s["year"] not in keep:
            continue
        for p in _api(cfg["listing"]["post_endpoint"],
                      {"categoryID": s["category_id"], "condition": cfg["listing"]["condition"]},
                      cfg, lang_cfg):
            yield {
                "url": (p.get("fileLink") or "").strip(),
                "post_id": p.get("postID"),
                "lang": lang_cfg["lang_id"],
                "title": " ".join(str(p.get("title") or "").split()),
                "published": str(p.get("publishDate") or "")[:10] or None,
                "category_year": s["year"],
                "section": s["section"],
            }


# ---------- Manifest + Bronze ----------

def load_manifest() -> pl.DataFrame:
    if not MANIFEST_PATH.exists():
        return pl.DataFrame(schema=MANIFEST_SCHEMA)
    df = pl.read_parquet(MANIFEST_PATH)
    missing = [c for c in MANIFEST_SCHEMA if c not in df.columns]
    df = df.with_columns([pl.lit(_MANIFEST_DEFAULTS.get(c)).alias(c) for c in missing])
    return df.select([pl.col(c).cast(t) for c, t in MANIFEST_SCHEMA.items()])


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


def _load_download_errors() -> dict[str, dict]:
    if not ERRORS_PATH.exists():
        return {}
    with ERRORS_PATH.open(encoding="utf-8", newline="") as f:
        return {r["url"]: r for r in csv.DictReader(f)}


def _log_download_error(doc: dict, doc_type: str, period: str, status: int) -> None:
    """Ghi link tải lỗi vào download_errors.csv (một dòng mỗi URL, cập nhật last_seen).

    Link lỗi không vào manifest, nên lần backfill sau vẫn thử lại phòng khi nguồn sửa link.
    """
    rows = _load_download_errors()
    today = date.today().isoformat()
    prev = rows.get(doc["url"], {})
    rows[doc["url"]] = {"url": doc["url"], "lang": doc["lang"], "title": doc["title"],
                        "doc_type": doc_type, "period": period, "http_status": status,
                        "first_seen": prev.get("first_seen", today), "last_seen": today}
    ERRORS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ERRORS_PATH.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ERROR_FIELDS)
        w.writeheader()
        w.writerows({k: r.get(k, "") for k in ERROR_FIELDS} for r in rows.values())


def _log_link_anomaly(doc: dict, period: str, period_in_manifest: str) -> None:
    """Ghi vào link_anomalies.csv một mục có URL đã lưu cho kỳ khác (một dòng mỗi cặp url + title).

    Ghi cả khi --dry-run: đây là trạng thái của nguồn, không phải dữ liệu Bronze.
    """
    print(f"[crawl] Cảnh báo: '{doc['title']}' (kỳ {period}) trỏ tới URL đã lưu cho kỳ "
          f"{period_in_manifest}: {doc['url']}")
    exists = ANOMALIES_PATH.exists()
    if exists:
        with ANOMALIES_PATH.open(encoding="utf-8", newline="") as f:
            if any(r["url"] == doc["url"] and r["title"] == doc["title"] for r in csv.DictReader(f)):
                return
    ANOMALIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ANOMALIES_PATH.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ANOMALY_FIELDS)
        if not exists:
            w.writeheader()
        w.writerow({"title": doc["title"], "period_from_title": period, "url": doc["url"],
                    "period_in_manifest": period_in_manifest,
                    "detected_at": datetime.now().isoformat(timespec="seconds")})


def _url_ext(url: str) -> str:
    return Path(urlsplit(url).path).suffix.lower()


def detect_file_type(content: bytes) -> str | None:
    """'pdf' / 'jpg' / 'png' theo magic bytes, hoặc None nếu không phải định dạng hỗ trợ."""
    return next((ft for ft, magic in MAGIC.items() if content.startswith(magic)), None)


def _fetch_reason(lang: str, primary_lang: str, doc_type: str, period: str,
                  have_primary: set, cross_check: set) -> str | None:
    """'primary' | 'cross_check' | 'fallback', hoặc None nếu không cần tải bản này."""
    if lang == primary_lang:
        return "primary"
    if (lang, doc_type, period) in cross_check:
        return "cross_check"
    if period == "unknown" or (doc_type, period) in have_primary:
        return None
    return "fallback"


def _collect_listing(cfg: dict, max_years: int | None) -> tuple[list[dict], int]:
    """Bước 1: lấy toàn bộ danh sách (mọi ngôn ngữ), giữ các mục cần lấy kèm doc_type và kỳ."""
    entries, skipped = [], 0
    for lang_cfg in cfg["listing"]["languages"]:  # bản chính trước
        for doc in list_documents(cfg, lang_cfg, max_years):
            doc_type = classify(doc["title"])
            if doc_type is None:
                skipped += 1
                continue
            if not doc["url"] or doc["url"] == "#":
                print(f"[crawl] Chưa có file (sắp công bố {doc['published']}): {doc['title']}")
                continue
            if _url_ext(doc["url"]) not in DOWNLOAD_EXTS:
                print(f"[crawl] Bỏ qua định dạng chưa hỗ trợ: {doc['title']} -> {doc['url'][-40:]}")
                continue
            period = parse_period(doc["title"], doc_type, default_year=doc["category_year"])
            if period and period[:4] != str(doc["category_year"]):
                print(f"[crawl] Lưu ý: kỳ {period} theo tiêu đề khác năm danh mục {doc['category_year']}: "
                      f"{doc['title']}")
            entries.append({**doc, "doc_type": doc_type, "period": period or "unknown",
                            "referer": lang_cfg["referer"]})
    return entries, skipped


def _candidates(group: list[dict]) -> str:
    return ";".join(sorted({f"{e['doc_type']}:{e['period']}" for e in group}))


def _pending_doc_type(group: list[dict]) -> str:
    types = {e["doc_type"] for e in group}
    return types.pop() if len(types) == 1 else PENDING_DOC_TYPE


def _mark_pending(mf: pl.DataFrame, url: str, doc_type: str, candidates: str) -> pl.DataFrame:
    hit = pl.col("url") == url
    return mf.with_columns(
        pl.when(hit).then(pl.lit(PENDING)).otherwise(pl.col("period")).alias("period"),
        pl.when(hit).then(pl.lit(doc_type)).otherwise(pl.col("doc_type")).alias("doc_type"),
        pl.when(hit).then(True).otherwise(pl.col("period_ambiguous")).alias("period_ambiguous"),
        pl.when(hit).then(pl.lit(candidates)).otherwise(pl.col("period_candidates")).alias("period_candidates"),
    )


def resolve_period(url: str, doc_type: str, period: str) -> None:
    """Gán kỳ thật cho file period=pending sau khi đối chiếu ngày báo cáo trong PDF.

    Giữ period_ambiguous = true để crawler không đánh dấu pending lại và để còn dấu vết.
    """
    mf = load_manifest()
    row = mf.filter(pl.col("url") == url)
    if row.height != 1 or row["period"][0] != PENDING:
        raise ValueError(f"[crawl] Không có file pending với URL {url}")
    hit = pl.col("url") == url
    _save_manifest(mf.with_columns(
        pl.when(hit).then(pl.lit(period)).otherwise(pl.col("period")).alias("period"),
        pl.when(hit).then(pl.lit(doc_type)).otherwise(pl.col("doc_type")).alias("doc_type"),
    ))
    print(f"[crawl] Gán kỳ {doc_type} {period} cho {url}")


def fill_text_layer() -> int:
    """Điền has_text_layer cho các PDF trong manifest còn null (file tải trước khi có cột này)."""
    mf = load_manifest()
    todo = mf.filter(pl.col("has_text_layer").is_null() & (pl.col("file_type") == "pdf")
                     & pl.col("path").is_not_null())
    if todo.height == 0:
        return 0
    values = {r["url"]: has_text_layer(ROOT / r["path"]) for r in todo.iter_rows(named=True)}
    _save_manifest(mf.with_columns(
        pl.col("url").replace_strict(values, default=pl.col("has_text_layer"), return_dtype=pl.Boolean)
        .alias("has_text_layer")))
    print(f"[crawl] Điền has_text_layer cho {len(values)} file.")
    return len(values)


def crawl(backfill: bool = False, dry_run: bool = False) -> list[dict]:
    cfg = load_config()
    missing = unfilled_fields(cfg)
    if missing:
        raise SystemExit(f"[crawl] sources.yaml thiếu: {', '.join(missing)}")

    primary_lang = cfg["listing"]["languages"][0]["lang_id"]
    cross_check = {(c["lang_id"], c["doc_type"], str(c["period"]))
                   for c in cfg["listing"].get("cross_check") or []}
    mf = load_manifest()
    known = {r["url"]: r for r in mf.iter_rows(named=True)}
    dead = {u for u, r in _load_download_errors().items()
            if str(r.get("http_status")) in {str(s) for s in DEAD_LINK_STATUS}}
    seen_periods = {(r["lang"], r["doc_type"], r["period"]): r["sha256"]
                    for r in mf.iter_rows(named=True) if r["period"] != PENDING}
    # Các kỳ đã có bản chính: bản dự phòng của các kỳ này không cần tải
    have_primary = {(r["doc_type"], r["period"]) for r in mf.iter_rows(named=True)
                    if r["lang"] == primary_lang}
    max_years = None if backfill else cfg["incremental_years"]
    new_rows: list[dict] = []
    failed = covered = 0

    entries, skipped = _collect_listing(cfg, max_years)
    groups: dict[str, list[dict]] = {}
    for e in entries:  # bước 2: nhóm theo URL (giữ thứ tự: bản chính trước)
        groups.setdefault(e["url"], []).append(e)

    for url, group in groups.items():  # bước 3: mỗi URL xử lý một lần
        ambiguous = len({e["period"] for e in group}) > 1  # nguồn trỏ một URL cho nhiều kỳ
        old = known.get(url)
        if ambiguous:
            for e in group:
                _log_link_anomaly(e, e["period"], old["period"] if old else PENDING)
        if old is not None:
            if ambiguous and not old["period_ambiguous"]:
                print(f"[crawl] Đánh dấu period=pending cho file đã lưu: {url}")
                if not dry_run:
                    mf = _mark_pending(mf, url, _pending_doc_type(group), _candidates(group))
                    _save_manifest(mf)
            elif not ambiguous and old["period"] not in (group[0]["period"], PENDING) \
                    and not old["period_ambiguous"]:
                _log_link_anomaly(group[0], group[0]["period"], old["period"])
            continue

        reasons = [r for e in group
                   if (r := _fetch_reason(e["lang"], primary_lang, e["doc_type"], e["period"],
                                          have_primary, cross_check))]
        if not reasons:
            covered += 1
            continue
        reason = min(reasons, key=_REASON_RANK.get)
        doc = next(e for e in group if _fetch_reason(e["lang"], primary_lang, e["doc_type"], e["period"],
                                                     have_primary, cross_check) == reason)
        lang = doc["lang"]
        doc_type, period = (_pending_doc_type(group), PENDING) if ambiguous else (doc["doc_type"], doc["period"])
        candidates = _candidates(group) if ambiguous else None

        if dry_run:
            note = "  [link đã 404, sẽ thử lại]" if url in dead else ""
            note += f"  [nhiều kỳ: {candidates}]" if ambiguous else ""
            print(f"[crawl] (dry-run) {lang} {reason:<11} {doc_type:<18} {period:<8} "
                  f"{doc['published']}  {doc['title']}{note}")
            if reason == "primary" and url not in dead and not ambiguous:
                have_primary.add((doc_type, period))
            new_rows.append({**doc, "doc_type": doc_type, "period": period, "fetch_reason": reason,
                             "known_dead": url in dead, "period_ambiguous": ambiguous})
            continue

        try:
            r = get(url, referer=doc["referer"], kind="pdf")
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status not in DEAD_LINK_STATUS:
                raise
            print(f"[crawl] Link chết (HTTP {status}), bỏ qua: {doc['title']} -> {url}")
            _log_download_error(doc, doc_type, period, status)
            failed += 1
            continue
        content = r.content
        file_type = detect_file_type(content)
        if file_type is None:
            print(f"[crawl] Nội dung tải về không phải PDF/ảnh, bỏ qua: {url}")
            continue

        sha = hashlib.sha256(content).hexdigest()
        out_dir = BRONZE_ROOT / f"doc_type={doc_type}" / f"ingestion_date={date.today().isoformat()}"
        out_dir.mkdir(parents=True, exist_ok=True)
        file_path = out_dir / f"{cfg['symbol']}_{period.replace('-', '')}_{sha[:8]}.{file_type}"
        if not file_path.exists():  # Bronze append-only: không ghi đè
            file_path.write_bytes(content)

        prev_sha = seen_periods.get((lang, doc_type, period))
        meta = {
            "url": url, "post_id": doc["post_id"], "lang": lang, "fetch_reason": reason,
            "title": doc["title"], "doc_type": doc_type, "file_type": file_type,
            "has_text_layer": has_text_layer(file_path) if file_type == "pdf" else False,
            "period": period, "period_ambiguous": ambiguous, "period_candidates": candidates,
            "published": doc["published"], "category_year": doc["category_year"],
            "assurance_level": assurance_level(doc["title"], doc["doc_type"], cfg, lang),
            "sha256": sha, "size": len(content), "http_status": r.status_code,
            "is_restatement": not ambiguous and prev_sha is not None and prev_sha != sha,
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
        }
        meta_path = file_path.with_name(file_path.name + ".meta.json")
        if not meta_path.exists():
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        row = {**meta, "path": _rel(file_path)}
        mf = pl.concat([mf, pl.DataFrame([row], schema=MANIFEST_SCHEMA)])
        _save_manifest(mf)  # lưu sau mỗi file để dừng giữa chừng không mất trạng thái
        known[url] = row
        if not ambiguous:
            seen_periods[(lang, doc_type, period)] = sha
            if reason == "primary":
                have_primary.add((doc_type, period))
        new_rows.append(row)
        print(f"[bronze] {lang} {reason} {doc_type} {period} <- {doc['title']}")

    if not dry_run:
        fill_text_layer()
    print(f"[crawl] Bỏ qua {skipped} tài liệu không thuộc loại cần lấy; "
          f"{covered} bản dự phòng không cần vì đã có bản chính.")
    if failed:
        print(f"[crawl] Link chết: {failed} (xem {_rel(ERRORS_PATH)}).")
    if new_rows:
        print(f"[crawl] Tài liệu mới: {len(new_rows)}" + (" (dry-run, chưa tải)" if dry_run else ""))
    else:
        print("[crawl] Không có tài liệu mới.")
    return new_rows


def missing_files(mf: pl.DataFrame | None = None) -> pl.DataFrame:
    """Dòng manifest mà file Bronze không có trên máy (repo không chứa PDF: clone mới, runner GitHub Actions)."""
    mf = load_manifest() if mf is None else mf
    return mf.filter(~pl.col("path").map_elements(lambda p: (ROOT / p).exists(), return_dtype=pl.Boolean))


def restore_missing(mf: pl.DataFrame | None = None) -> dict:
    """Tải lại từ URL gốc các file Bronze có trong manifest mà thiếu trên máy, giữ nguyên đường dẫn.

    Chỉ ghi khi sha256 nội dung tải về trùng sha256 trong manifest (Bronze không đổi); khác thì không ghi, báo lại.
    Link chết ghi download_errors.csv như crawl; 403 dừng (PermissionError, không vượt chặn)."""
    todo = missing_files(mf)
    out = {"missing": todo.height, "restored": 0, "mismatch": 0, "failed": 0}
    for r in todo.iter_rows(named=True):
        try:
            content = get(r["url"], kind="pdf").content
        except (requests.HTTPError, RobotsDisallowed) as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            print(f"[crawl] Không tải lại được (HTTP {status}): {r['url']}")
            if status in DEAD_LINK_STATUS:
                _log_download_error(r, r["doc_type"], r["period"], status)
            out["failed"] += 1
            continue
        if hashlib.sha256(content).hexdigest() != r["sha256"]:
            print(f"[crawl] Nội dung trên nguồn đã đổi (sha256 khác manifest), không ghi: {r['url']}")
            out["mismatch"] += 1
            continue
        path = ROOT / r["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        out["restored"] += 1
    if out["missing"]:
        print(f"[bronze] Tải lại file thiếu: {out['restored']}/{out['missing']} "
              f"(sha256 khác: {out['mismatch']}, lỗi: {out['failed']})")
    return out


def main(argv: list[str]) -> int:
    if "--restore" in argv:
        res = restore_missing()
        return 0 if res["restored"] == res["missing"] else 1
    crawl(backfill="--backfill" in argv, dry_run="--dry-run" in argv)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
