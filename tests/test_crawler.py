"""Test crawler với API giả: không gọi mạng thật. Nội dung PDF là byte giả, không phải số liệu."""
import json
from urllib.parse import parse_qs, urlsplit

import polars as pl
import pytest
import requests

from src.ingestion import mwg_crawler

FS, MONTHLY = "Báo cáo tài chính, quản trị, thường niên", "Báo cáo kết quả kinh doanh theo tháng"


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
        self.years, self.posts = years, posts
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
            data = self.years
        elif "PostAPI" in url:
            data = self.posts.get(int(q["categoryID"]), [])
        else:
            is_jpg = urlsplit(url).path.endswith(".jpg")
            return FakeResp(url, content=self.jpg_content if is_jpg else self.pdf_content)
        for d in data:
            d["totalRecord"] = len(data)
        return FakeResp(url, {"code": 0, "data": data})

    def downloads(self):
        return [u for u, kind in self.calls if kind == "pdf"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(mwg_crawler, "MANIFEST_PATH", tmp_path / "_state" / "manifest.parquet")
    monkeypatch.setattr(mwg_crawler, "BRONZE_ROOT", tmp_path / "bronze" / "source=mwg_ir")
    monkeypatch.setattr(mwg_crawler, "ERRORS_PATH", tmp_path / "_state" / "download_errors.csv")

    def install(api):
        monkeypatch.setattr(mwg_crawler, "get", api)
        return api
    return tmp_path, install


def _sample_api(**kw):
    years = [_year(429, "2026", [(431, MONTHLY), (430, FS)]),
             _year(205, "2025", [(208, MONTHLY), (209, FS)]),
             _year(193, "2024", [(194, MONTHLY), (195, FS)])]
    posts = {
        430: [_post(1, "Báo Cáo Tài Chính Hợp Nhất Quý 3 Năm 2026", "https://cdn.test/a/q3.pdf"),
              _post(2, "Báo Cáo Tài Chính Riêng Quý 3 Năm 2026", "https://cdn.test/a/rieng.pdf"),
              _post(3, "Báo Cáo Tài Chính Hợp Nhất Quý 2 Năm 2026", "https://cdn.test/a/q2.pdf", 2, active=False)],
        431: [_post(4, "Cập Nhật Tình Hình Kinh Doanh 10 Tháng 2026", ""),
              _post(5, "Cập Nhật Tình Hình Kinh Doanh 9 Tháng 2026", "https://cdn.test/a/m9.pdf")],
        209: [_post(6, "Báo Cáo Tài Chính Hợp Nhất Kiểm Toán Năm 2025", "https://cdn.test/b/y.pdf", 4)],
        208: [_post(7, "Báo cáo tóm tắt KQKD 8 tháng đầu năm 2025", "https://cdn.test/b/m8.jpg"),
              _post(10, "Báo cáo tóm tắt KQKD 7 tháng đầu năm 2025", "https://cdn.test/b/m7.docx")],
        195: [_post(8, "Báo Cáo Tài Chính Hợp Nhất Quý 1 Năm 2024", "https://cdn.test/c/q1.pdf", 1)],
    }
    return FakeApi(years, posts, **kw)


def test_crawl_tai_dung_tai_lieu_va_ghi_bronze(env):
    tmp, install = env
    api = install(_sample_api())
    rows = mwg_crawler.crawl()

    # incremental: chỉ 2 năm mới nhất; bỏ BCTC riêng, bài ẩn, link rỗng, định dạng .docx
    assert sorted(api.downloads()) == ["https://cdn.test/a/m9.pdf", "https://cdn.test/a/q3.pdf",
                                       "https://cdn.test/b/m8.jpg", "https://cdn.test/b/y.pdf"]
    assert {(r["doc_type"], r["file_type"], r["period"], r["assurance_level"]) for r in rows} == {
        ("FS_CONSOLIDATED_Q", "pdf", "2026-Q3", 1), ("MONTHLY_RESULT", "pdf", "2026-M09", 1),
        ("FS_CONSOLIDATED_Y", "pdf", "2025", 3), ("MONTHLY_RESULT", "jpg", "2025-M08", 1)}

    pdfs = sorted((tmp / "bronze").rglob("*.pdf"))
    assert len(pdfs) == 3
    q3 = next(p for p in pdfs if "MWG_2026Q3_" in p.name)
    assert q3.parent.parent.name == "doc_type=FS_CONSOLIDATED_Q"
    assert q3.parent.name.startswith("ingestion_date=")
    meta = json.loads(q3.with_name(q3.name + ".meta.json").read_text(encoding="utf-8"))
    for key in ("url", "title", "doc_type", "file_type", "period", "published", "sha256", "size",
                "fetched_at", "http_status"):
        assert key in meta
    assert q3.name == f"MWG_2026Q3_{meta['sha256'][:8]}.pdf"

    jpgs = list((tmp / "bronze").rglob("*.jpg"))
    assert [j.name[:12] for j in jpgs] == ["MWG_2025M08_"]
    assert jpgs[0].with_name(jpgs[0].name + ".meta.json").exists()

    mf = pl.read_parquet(mwg_crawler.MANIFEST_PATH)
    assert mf.height == 4 and set(mf["url"]) == {r["url"] for r in rows}


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
    assert "https://cdn.test/c/q1.pdf" in api.downloads()


def test_dry_run_khong_tai_va_khong_ghi(env):
    tmp, install = env
    api = install(_sample_api())
    rows = mwg_crawler.crawl(dry_run=True)
    assert len(rows) == 4
    assert api.downloads() == []
    assert not mwg_crawler.MANIFEST_PATH.exists()
    assert not (tmp / "bronze").exists()


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


def test_manifest_cu_chua_co_file_type(env):
    # manifest tạo trước khi có cột file_type: đọc lên coi là pdf, lần chạy sau ghi tiếp được
    _, install = env
    old_schema = {k: v for k, v in mwg_crawler.MANIFEST_SCHEMA.items() if k != "file_type"}
    old = pl.DataFrame([{"url": "https://cdn.test/a/q3.pdf", "post_id": 1, "doc_type": "FS_CONSOLIDATED_Q",
                         "period": "2026-Q3", "sha256": "x" * 64}], schema=old_schema)
    mwg_crawler.MANIFEST_PATH.parent.mkdir(parents=True)
    old.write_parquet(mwg_crawler.MANIFEST_PATH)

    mf = mwg_crawler.load_manifest()
    assert mf.columns == list(mwg_crawler.MANIFEST_SCHEMA) and mf["file_type"].to_list() == ["pdf"]
    api = install(_sample_api())
    mwg_crawler.crawl()
    assert "https://cdn.test/a/q3.pdf" not in api.downloads()
    assert pl.read_parquet(mwg_crawler.MANIFEST_PATH).height == 4


def test_ban_dieu_chinh_cung_ky_duoc_danh_dau(env):
    _, install = env
    install(_sample_api())
    mwg_crawler.crawl()
    api = _sample_api(pdf_content=b"%PDF-1.4 ban dieu chinh")
    api.posts[430].append(_post(9, "Báo Cáo Tài Chính Hợp Nhất Quý 3 Năm 2026",
                                "https://cdn.test/a/q3-v2.pdf"))
    install(api)
    rows = mwg_crawler.crawl()
    assert [(r["period"], r["is_restatement"]) for r in rows] == [("2026-Q3", True)]


DEAD = "https://cdn.test/a/q3.pdf"


@pytest.mark.parametrize("status", [404, 410])
def test_link_chet_ghi_lai_va_tai_tiep(env, status):
    _, install = env
    api = install(_sample_api(errors={DEAD: status}))
    rows = mwg_crawler.crawl()

    assert DEAD in api.downloads()
    assert {r["url"] for r in rows} == {"https://cdn.test/a/m9.pdf", "https://cdn.test/b/y.pdf",
                                        "https://cdn.test/b/m8.jpg"}
    errors = mwg_crawler.ERRORS_PATH.read_text(encoding="utf-8")
    assert DEAD in errors and str(status) in errors
    # link chết không vào manifest, lần sau vẫn thử lại; file lỗi không bị nhân dòng
    api2 = install(_sample_api(errors={DEAD: status}))
    mwg_crawler.crawl()
    assert api2.downloads() == [DEAD]
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
