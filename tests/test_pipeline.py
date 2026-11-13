"""run_pipeline.py: khóa chống chạy đồng thời, cờ --force / --crawl (không gọi mạng)."""
import json
import os

import polars as pl
import pytest

import run_pipeline as P


def test_khoa_song_chan_lan_sau_khoa_chet_thi_go(tmp_path):
    lock = tmp_path / "pipeline.lock"
    P.acquire_lock(lock)
    assert json.loads(lock.read_text(encoding="utf-8"))["pid"] == os.getpid()
    with pytest.raises(P.PipelineLocked):  # chính tiến trình này đang giữ khóa: còn sống
        P.acquire_lock(lock)
    P.release_lock(lock)
    assert not lock.exists()
    lock.write_text(json.dumps({"pid": 2 ** 22 + 12345, "started_at": "x"}), encoding="utf-8")  # PID không tồn tại
    P.acquire_lock(lock)
    assert json.loads(lock.read_text(encoding="utf-8"))["pid"] == os.getpid()
    P.release_lock(lock)


def test_khong_go_khoa_cua_tien_trinh_khac(tmp_path):
    lock = tmp_path / "pipeline.lock"
    lock.write_text(json.dumps({"pid": 1, "started_at": "x"}), encoding="utf-8")
    P.release_lock(lock)
    assert lock.exists()


@pytest.mark.parametrize("argv, crawled", [(["--force"], False), (["--force", "--crawl"], True), ([], True),
                                           (["--no-crawl", "--force"], False), (["--backfill"], True)])
def test_force_khong_crawl_tru_khi_co_crawl(monkeypatch, tmp_path, argv, crawled):
    calls = []
    monkeypatch.setattr(P, "LOCK", tmp_path / "pipeline.lock")
    monkeypatch.setattr(P.mwg_crawler, "crawl", lambda backfill=False: calls.append("crawl") or ["new.pdf"])
    monkeypatch.setattr(P.mwg_crawler, "missing_files", lambda: pl.DataFrame({"path": []}, schema={"path": pl.Utf8}))
    monkeypatch.setattr(P.mwg_crawler, "restore_missing", lambda: calls.append("restore"))
    for mod in (P.run_extraction, P.bronze_to_silver, P.silver_to_gold):
        monkeypatch.setattr(mod, "run", lambda: None)
    monkeypatch.setattr(P, "acquire_lock", lambda path=None: None)
    monkeypatch.setattr(P, "release_lock", lambda path=None: None)
    assert P.main(argv) == 0
    assert ("crawl" in calls) == crawled
