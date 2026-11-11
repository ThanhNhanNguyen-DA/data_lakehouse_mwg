"""Test crawler với API giả: không gọi mạng thật. Nội dung PDF là byte giả, không phải số liệu."""
import json
from urllib.parse import parse_qs, urlsplit

import polars as pl
import pytest

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
    def __init__(self, url, body=None, content=b""):
        self.url, self._body, self.content, self.status_code = url, body, content, 200

    def json(self):
        return self._body


class FakeApi:
    def __init__(self, years, posts, pdf_content=b"%PDF-1.4 fake"):
        self.years, self.posts, self.pdf_content = years, posts, pdf_content
        self.calls = []

    def __call__(self, url, referer=None, *, kind="page", stream=False):
        self.calls.append((url, kind))
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        if "CategoryAPI" in url:
            data = self.years
        elif "PostAPI" in url:
            data = self.posts.get(int(q["categoryID"]), [])
        else:
            return FakeResp(url, content=self.pdf_content)
        for d in data:
            d["totalRecord"] = len(data)
        return FakeResp(url, {"code": 0, "data": data})

    def downloads(self):
        return [u for u, kind in self.calls if kind == "pdf"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(mwg_crawler, "MANIFEST_PATH", tmp_path / "_state" / "manifest.parquet")
    monkeypatch.setattr(mwg_crawler, "BRONZE_ROOT", tmp_path / "bronze" / "source=mwg_ir")

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
        208: [_post(7, "Báo cáo tóm tắt KQKD 8 tháng đầu năm 2025", "https://cdn.test/b/m8.jpg")],
        195: [_post(8, "Báo Cáo Tài Chính Hợp Nhất Quý 1 Năm 2024", "https://cdn.test/c/q1.pdf", 1)],
    }
    return FakeApi(years, posts, **kw)


def test_crawl_tai_dung_tai_lieu_va_ghi_bronze(env):
    tmp, install = env
    api = install(_sample_api())
    rows = mwg_crawler.crawl()

    # incremental: chỉ 2 năm mới nhất; bỏ BCTC riêng, bài ẩn, link rỗng, file .jpg
    assert sorted(api.downloads()) == ["https://cdn.test/a/m9.pdf", "https://cdn.test/a/q3.pdf",
                                       "https://cdn.test/b/y.pdf"]
    assert {(r["doc_type"], r["period"], r["assurance_level"]) for r in rows} == {
        ("FS_CONSOLIDATED_Q", "2026-Q3", 1), ("MONTHLY_RESULT", "2026-M09", 1),
        ("FS_CONSOLIDATED_Y", "2025", 3)}

    pdfs = sorted((tmp / "bronze").rglob("*.pdf"))
    assert len(pdfs) == 3
    q3 = next(p for p in pdfs if "MWG_2026Q3_" in p.name)
    assert q3.parent.parent.name == "doc_type=FS_CONSOLIDATED_Q"
    assert q3.parent.name.startswith("ingestion_date=")
    meta = json.loads(q3.with_name(q3.name + ".meta.json").read_text(encoding="utf-8"))
    for key in ("url", "title", "doc_type", "period", "published", "sha256", "size",
                "fetched_at", "http_status"):
        assert key in meta
    assert q3.name == f"MWG_2026Q3_{meta['sha256'][:8]}.pdf"

    mf = pl.read_parquet(mwg_crawler.MANIFEST_PATH)
    assert mf.height == 3 and set(mf["url"]) == {r["url"] for r in rows}


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
    assert len(rows) == 3
    assert api.downloads() == []
    assert not mwg_crawler.MANIFEST_PATH.exists()
    assert not (tmp / "bronze").exists()


def test_noi_dung_khong_phai_pdf_thi_bo_qua(env):
    tmp, install = env
    install(_sample_api(pdf_content=b"<html>error</html>"))
    assert mwg_crawler.crawl() == []
    assert not list(tmp.rglob("*.pdf"))


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
