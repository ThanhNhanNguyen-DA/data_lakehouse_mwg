"""Test crawler với API giả: không gọi mạng thật. Nội dung file là byte giả, không phải số liệu."""
import copy
import csv
import json
from urllib.parse import parse_qs, urlsplit

import polars as pl
import pytest
import requests

from src.ingestion import mwg_crawler

FS_VI, MONTHLY_VI = "Báo cáo tài chính, quản trị, thường niên", "Báo cáo kết quả kinh doanh theo tháng"
FS_EN = "ANNUAL, CORPORATE GOVERNANCE & FINANCIAL REPORTS"
CDN = "https://cdn.test"


def _year(cid, name, children):
    return {"categoryID": cid, "categoryName": name, "isActived": True, "isDeleted": False,
            "childrenIDList": ",".join(str(c) for c, _ in children),
            "childrenNameList": ";".join(n for _, n in children)}


def _post(pid, title, link, quarter=3, active=True):
    return {"postID": pid, "title": title, "fileLink": link, "publishDate": "2026-10-28T00:00:00",
            "quarter": quarter, "isActived": active, "isDeleted": False}


class FakeResp:
    def __init__(self, url, body=None, content=b"", status_code=200):
        self.url, self._body, self.content, self.status_code = url, body, content, status_code

    def json(self):
        return self._body


class FakeApi:
    def __init__(self, years, posts, pdf_content=b"%PDF-1.4 fake",
                 jpg_content=b"\xff\xd8\xff\xe0 fake", errors=None):
        self.years, self.posts = years, posts  # years: lang -> [category năm]
        self.pdf_content, self.jpg_content = pdf_content, jpg_content
        self.errors = errors or {}  # url -> HTTP status hoặc exception
        self.calls = []

    def __call__(self, url, referer=None, *, kind="page", stream=False):
        self.calls.append((url, kind))
        err = self.errors.get(url)
        if isinstance(err, Exception):
            raise err
        if err:
            raise requests.HTTPError(f"{err}", response=FakeResp(url, status_code=err))
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        if "CategoryAPI" in url:
            data = self.years.get(q["langID"], [])
        elif "PostAPI" in url:
            data = self.posts.get(int(q["categoryID"]), [])
            if not data:
                return FakeResp(url, {"code": mwg_crawler.API_CODE_NO_DATA, "data": None})
        else:
            is_jpg = urlsplit(url).path.endswith(".jpg")
            return FakeResp(url, content=self.jpg_content if is_jpg else self.pdf_content)
        for d in data:
            d["totalRecord"] = len(data)
        return FakeResp(url, {"code": 0, "data": data})

    def downloads(self):
        return sorted(u.removeprefix(CDN) for u, kind in self.calls if kind == "pdf")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(mwg_crawler, "MANIFEST_PATH", tmp_path / "_state" / "manifest.parquet")
    monkeypatch.setattr(mwg_crawler, "BRONZE_ROOT", tmp_path / "bronze" / "source=mwg_ir")
    monkeypatch.setattr(mwg_crawler, "ERRORS_PATH", tmp_path / "_state" / "download_errors.csv")
    monkeypatch.setattr(mwg_crawler, "ANOMALIES_PATH", tmp_path / "_state" / "link_anomalies.csv")
    monkeypatch.setattr(mwg_crawler, "has_text_layer", lambda path: True)

    def install(api):
        monkeypatch.setattr(mwg_crawler, "get", api)
        return api
    return tmp_path, install


def _sample_api(**kw):
    years = {
        "vi-VN": [_year(429, "2026", [(431, MONTHLY_VI), (430, FS_VI)]),
                  _year(205, "2025", [(208, MONTHLY_VI), (209, FS_VI)]),
                  _year(193, "2024", [(194, MONTHLY_VI), (195, FS_VI)])],
        "en-US": [_year(432, "2026", [(434, "MONTHLY PERFORMANCE UPDATES"), (433, FS_EN)]),
                  _year(211, "2025", [(212, "MONTHLY PERFORMANCE UPDATES"), (213, FS_EN)]),
                  _year(196, "2024", [(197, "MONTHLY PERFORMANCE UPDATES"), (198, FS_EN)])],
    }
    posts = {
        430: [_post(1, "Báo Cáo Tài Chính Hợp Nhất Quý 3 Năm 2026", f"{CDN}/vi/q3-2026.pdf"),
              _post(2, "Báo Cáo Tài Chính Riêng Quý 3 Năm 2026", f"{CDN}/vi/rieng.pdf"),
              _post(3, "Báo Cáo Tài Chính Hợp Nhất Quý 2 Năm 2026", f"{CDN}/vi/q2-2026.pdf", 2, active=False)],
        431: [_post(4, "Cập Nhật Tình Hình Kinh Doanh 10 Tháng 2026", ""),
              _post(5, "Cập Nhật Tình Hình Kinh Doanh 9 Tháng 2026", f"{CDN}/vi/m9.pdf")],
        209: [_post(6, "Báo Cáo Tài Chính Hợp Nhất Kiểm Toán Năm 2025", f"{CDN}/vi/fy2025.pdf", 4)],
        208: [_post(7, "Báo cáo tóm tắt KQKD 8 tháng đầu năm 2025", f"{CDN}/vi/m8.jpg"),
              _post(10, "Báo cáo tóm tắt KQKD 7 tháng đầu năm 2025", f"{CDN}/vi/m7.docx")],
        195: [_post(8, "Báo Cáo Tài Chính Hợp Nhất Quý 1 Năm 2024", f"{CDN}/vi/q1-2024.pdf", 1)],
        433: [_post(21, "Consolidated FS Q3/2026", f"{CDN}/en/q3-2026.pdf"),
              _post(22, "Separate FS Q3/2026", f"{CDN}/en/sep-q3-2026.pdf"),
              _post(23, "Audited Consolidated FS 1H2026", f"{CDN}/en/1h2026.pdf", 2)],
        213: [_post(24, "Audited Consolidated FS FY2025", f"{CDN}/en/fy2025.pdf", 4)],
        198: [_post(25, "Consolidated FS Q1/2024", f"{CDN}/en/q1-2024.pdf", 1)],
        434: [_post(26, "YTD September 2026 report", f"{CDN}/en/ytd-sep-2026.pdf"),
              _post(27, "YTD August 2026 report", f"{CDN}/en/ytd-aug-2026.pdf"),
              _post(28, "MWG | Investor meeting - Q3 2026", f"{CDN}/en/im-q3-2026.pdf")],
    }
    return FakeApi(years, posts, **kw)


def _with_config(monkeypatch, **changes):
    cfg = copy.deepcopy(mwg_crawler.load_config())
    cfg["listing"].update(changes)
    monkeypatch.setattr(mwg_crawler, "load_config", lambda *a, **k: cfg)


# ---------- Bản chính vi-VN + dự phòng en-US ----------

def test_crawl_tai_ban_chinh_va_du_phong_khi_thieu(env):
    tmp, install = env
    api = install(_sample_api())
    rows = mwg_crawler.crawl()

    # vi-VN: bỏ BCTC riêng, bài ẩn, link rỗng, .docx. en-US: chỉ 1H2026 và báo cáo tháng 8/2026
    # vì vi-VN không có hai kỳ này; Q3/2026, FY2025, tháng 9/2026 đã có bản vi-VN.
    assert api.downloads() == ["/en/1h2026.pdf", "/en/ytd-aug-2026.pdf", "/vi/fy2025.pdf",
                               "/vi/m8.jpg", "/vi/m9.pdf", "/vi/q3-2026.pdf"]
    got = {(r["lang"], r["fetch_reason"], r["doc_type"], r["file_type"], r["period"],
            r["assurance_level"]) for r in rows}
    assert got == {
        ("vi-VN", "primary", "FS_CONSOLIDATED_Q", "pdf", "2026-Q3", 1),
        ("vi-VN", "primary", "MONTHLY_RESULT", "pdf", "2026-M09", 1),
        ("vi-VN", "primary", "FS_CONSOLIDATED_Y", "pdf", "2025", 3),
        ("vi-VN", "primary", "MONTHLY_RESULT", "jpg", "2025-M08", 1),
        ("en-US", "fallback", "FS_CONSOLIDATED_H", "pdf", "2026-H1", 2),
        ("en-US", "fallback", "MONTHLY_RESULT", "pdf", "2026-M08", 1),
    }

    q3 = next((tmp / "bronze").rglob("MWG_2026Q3_*.pdf"))
    assert q3.parent.parent.name == "doc_type=FS_CONSOLIDATED_Q"
    assert q3.parent.name.startswith("ingestion_date=")
    meta = json.loads(q3.with_name(q3.name + ".meta.json").read_text(encoding="utf-8"))
    for key in ("url", "lang", "fetch_reason", "title", "doc_type", "file_type", "has_text_layer",
                "period", "published", "sha256", "size", "fetched_at", "http_status"):
        assert key in meta
    assert q3.name == f"MWG_2026Q3_{meta['sha256'][:8]}.pdf"

    mf = pl.read_parquet(mwg_crawler.MANIFEST_PATH)
    assert mf.height == 6 and set(mf["url"]) == {r["url"] for r in rows}
    assert mf.columns == list(mwg_crawler.MANIFEST_SCHEMA)


def test_ban_chinh_chet_link_thi_tai_ban_du_phong(env):
    _, install = env
    api = install(_sample_api(errors={f"{CDN}/vi/fy2025.pdf": 404}))
    rows = mwg_crawler.crawl()
    assert "/en/fy2025.pdf" in api.downloads()
    y = next(r for r in rows if r["period"] == "2025")
    assert (y["lang"], y["fetch_reason"], y["assurance_level"]) == ("en-US", "fallback", 3)


def test_cross_check_tai_ca_hai_ngon_ngu(env, monkeypatch):
    _, install = env
    _with_config(monkeypatch, cross_check=[
        {"lang_id": "en-US", "doc_type": "FS_CONSOLIDATED_Q", "period": "2026-Q3"}])
    api = install(_sample_api())
    rows = mwg_crawler.crawl()
    assert {"/vi/q3-2026.pdf", "/en/q3-2026.pdf"} <= set(api.downloads())
    q3 = {(r["lang"], r["fetch_reason"], r["is_restatement"]) for r in rows if r["period"] == "2026-Q3"}
    # hai ngôn ngữ cùng kỳ không phải bản điều chỉnh
    assert q3 == {("vi-VN", "primary", False), ("en-US", "cross_check", False)}


def test_chay_lan_hai_khong_tai_lai(env, capsys):
    _, install = env
    install(_sample_api())
    mwg_crawler.crawl()
    api2 = install(_sample_api())
    assert mwg_crawler.crawl() == []
    assert api2.downloads() == []
    assert "Không có tài liệu mới" in capsys.readouterr().out


def test_backfill_xem_tat_ca_cac_nam(env):
    _, install = env
    api = install(_sample_api())
    mwg_crawler.crawl(backfill=True)
    assert "/vi/q1-2024.pdf" in api.downloads()
    assert "/en/q1-2024.pdf" not in api.downloads()  # đã có bản vi-VN


# ---------- Dry-run ----------

def test_dry_run_khong_tai_va_khong_ghi(env):
    tmp, install = env
    api = install(_sample_api())
    rows = mwg_crawler.crawl(dry_run=True)
    assert len(rows) == 6
    assert api.downloads() == []
    assert not mwg_crawler.MANIFEST_PATH.exists()
    assert not (tmp / "bronze").exists()


def test_dry_run_tinh_den_link_chet_da_biet(env):
    _, install = env
    mwg_crawler.ERRORS_PATH.parent.mkdir(parents=True)
    with mwg_crawler.ERRORS_PATH.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=mwg_crawler.ERROR_FIELDS)
        w.writeheader()
        w.writerow({"url": f"{CDN}/vi/fy2025.pdf", "lang": "vi-VN", "http_status": 404})
    install(_sample_api())
    rows = mwg_crawler.crawl(dry_run=True)
    by_url = {r["url"].removeprefix(CDN): r for r in rows}
    assert by_url["/vi/fy2025.pdf"]["known_dead"] is True
    assert by_url["/en/fy2025.pdf"]["fetch_reason"] == "fallback"


# ---------- Manifest ----------

def test_manifest_cu_duoc_bo_sung_cot_moi(env):
    # manifest tạo trước khi có file_type, lang, fetch_reason, has_text_layer
    _, install = env
    old_cols = {k: v for k, v in mwg_crawler.MANIFEST_SCHEMA.items()
                if k not in ("file_type", "lang", "fetch_reason", "has_text_layer")}
    old = pl.DataFrame([{"url": f"{CDN}/vi/q3-2026.pdf", "post_id": 1, "doc_type": "FS_CONSOLIDATED_Q",
                         "period": "2026-Q3", "sha256": "x" * 64}], schema=old_cols)
    mwg_crawler.MANIFEST_PATH.parent.mkdir(parents=True)
    old.write_parquet(mwg_crawler.MANIFEST_PATH)

    mf = mwg_crawler.load_manifest()
    assert mf.columns == list(mwg_crawler.MANIFEST_SCHEMA)
    assert mf.select("lang", "fetch_reason", "file_type", "has_text_layer").row(0) == (
        "vi-VN", "primary", "pdf", None)
    api = install(_sample_api())
    mwg_crawler.crawl()
    # kỳ 2026-Q3 đã có bản vi-VN trong manifest cũ -> không tải lại, cũng không tải bản en-US
    assert "/vi/q3-2026.pdf" not in api.downloads()
    assert "/en/q3-2026.pdf" not in api.downloads()


def test_ghi_has_text_layer(env, monkeypatch):
    _, install = env
    monkeypatch.setattr(mwg_crawler, "has_text_layer", lambda path: path.suffix == ".pdf" and "2026H1" in path.name)
    install(_sample_api())
    rows = {r["period"]: r for r in mwg_crawler.crawl()}
    assert rows["2026-H1"]["has_text_layer"] is True       # en-US
    assert rows["2026-Q3"]["has_text_layer"] is False      # PDF scan
    assert rows["2025-M08"]["has_text_layer"] is False     # ảnh luôn cần OCR


def test_ban_dieu_chinh_cung_ky_cung_ngon_ngu_duoc_danh_dau(env):
    _, install = env
    install(_sample_api())
    mwg_crawler.crawl()
    api = _sample_api(pdf_content=b"%PDF-1.4 ban dieu chinh")
    api.posts[430].append(_post(9, "Báo Cáo Tài Chính Hợp Nhất Quý 3 Năm 2026", f"{CDN}/vi/q3-2026-v2.pdf"))
    install(api)
    rows = mwg_crawler.crawl()
    assert [(r["lang"], r["period"], r["is_restatement"]) for r in rows] == [("vi-VN", "2026-Q3", True)]


# ---------- Định dạng file ----------

def test_noi_dung_khong_phai_pdf_hay_anh_thi_bo_qua(env):
    tmp, install = env
    install(_sample_api(pdf_content=b"<html>error</html>", jpg_content=b"<html>error</html>"))
    assert mwg_crawler.crawl() == []
    assert not list((tmp / "bronze").rglob("MWG_*"))


def test_file_type_theo_noi_dung_khong_theo_duoi_url(env):
    _, install = env
    install(_sample_api(jpg_content=b"\x89PNG\r\n\x1a\n fake"))
    rows = mwg_crawler.crawl()
    m8 = next(r for r in rows if r["period"] == "2025-M08")
    assert m8["file_type"] == "png" and m8["path"].endswith(".png")


@pytest.mark.parametrize("content, expected", [
    (b"%PDF-1.4", "pdf"), (b"\xff\xd8\xff\xe0", "jpg"), (b"\x89PNG\r\n\x1a\n", "png"),
    (b"<html>", None), (b"", None),
])
def test_detect_file_type(content, expected):
    assert mwg_crawler.detect_file_type(content) == expected


# ---------- Lỗi tải ----------

DEAD = f"{CDN}/vi/q3-2026.pdf"


@pytest.mark.parametrize("status", [404, 410])
def test_link_chet_ghi_lai_va_tai_tiep(env, status):
    _, install = env
    api = install(_sample_api(errors={DEAD: status}))
    rows = mwg_crawler.crawl()

    assert "/vi/q3-2026.pdf" in api.downloads()
    # bản vi-VN chết -> tải bản en-US cùng kỳ thay thế
    assert ("en-US", "fallback") in {(r["lang"], r["fetch_reason"]) for r in rows if r["period"] == "2026-Q3"}
    errors = mwg_crawler.ERRORS_PATH.read_text(encoding="utf-8")
    assert DEAD in errors and str(status) in errors and "vi-VN" in errors
    # link chết không vào manifest, lần sau vẫn thử lại; file lỗi không bị nhân dòng
    api2 = install(_sample_api(errors={DEAD: status}))
    mwg_crawler.crawl()
    assert api2.downloads() == ["/vi/q3-2026.pdf"]
    assert mwg_crawler.ERRORS_PATH.read_text(encoding="utf-8").count(DEAD) == 1


def test_403_van_dung_ca_lan_chay(env):
    _, install = env
    install(_sample_api(errors={DEAD: PermissionError(f"403 Forbidden: {DEAD}")}))
    with pytest.raises(PermissionError):
        mwg_crawler.crawl()


def test_loi_http_khac_van_dung(env):
    _, install = env
    install(_sample_api(errors={DEAD: 401}))
    with pytest.raises(requests.HTTPError):
        mwg_crawler.crawl()


# ---------- Link bất thường: một URL cho hai kỳ ----------

def _api_with_shared_url():
    api = _sample_api()
    # giống lỗi thật "1H17" -> file Q3/2017: mục tháng 7 trỏ tới file tháng 9
    api.posts[431].append(_post(11, "Cập Nhật Tình Hình Kinh Doanh 7 Tháng 2026", f"{CDN}/vi/m9.pdf"))
    return api


def test_url_dung_chung_giua_hai_ky_ghi_link_anomalies(env):
    _, install = env
    install(_api_with_shared_url())
    rows = mwg_crawler.crawl()

    assert [r["period"] for r in rows if r["url"] == f"{CDN}/vi/m9.pdf"] == ["2026-M09"]  # không lưu trùng
    with mwg_crawler.ANOMALIES_PATH.open(encoding="utf-8") as f:
        anomalies = list(csv.DictReader(f))
    assert list(anomalies[0]) == mwg_crawler.ANOMALY_FIELDS
    assert [(a["title"], a["period_from_title"], a["url"], a["period_in_manifest"]) for a in anomalies] == [
        ("Cập Nhật Tình Hình Kinh Doanh 7 Tháng 2026", "2026-M07", f"{CDN}/vi/m9.pdf", "2026-M09")]

    install(_api_with_shared_url())
    mwg_crawler.crawl()  # chạy lại: không nhân dòng
    assert mwg_crawler.ANOMALIES_PATH.read_text(encoding="utf-8").count("7 Tháng 2026") == 1


def test_url_da_biet_cung_ky_khong_phai_bat_thuong(env):
    _, install = env
    install(_sample_api())
    mwg_crawler.crawl()
    install(_sample_api())
    mwg_crawler.crawl()
    assert not mwg_crawler.ANOMALIES_PATH.exists()
