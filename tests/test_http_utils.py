"""Test http_utils bằng session giả: không gọi mạng thật, không ngủ thật."""
import time
from urllib.robotparser import RobotFileParser

import pytest
import requests

from src.ingestion import http_utils


class FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text
        self.ok = status_code < 400

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(str(self.status_code))


class FakeSession:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = []
        self.headers = {"User-Agent": "test-agent"}

    def get(self, url, headers=None, timeout=None, stream=False):
        self.calls.append((url, headers))
        return FakeResponse(self.statuses.pop(0))


@pytest.fixture
def fake(monkeypatch):
    def install(statuses, robots_txt=None):
        session = FakeSession(statuses)
        monkeypatch.setattr(http_utils, "_session", session)
        monkeypatch.setattr(http_utils, "_last_request_at", None)
        monkeypatch.setattr(time, "sleep", lambda s: None)  # cả tenacity cũng dùng time.sleep
        rp = RobotFileParser()
        if robots_txt is None:
            rp.allow_all = True
        else:
            rp.parse(robots_txt.splitlines())
        monkeypatch.setattr(http_utils, "_robots", {"https://mwg.vn": rp})
        return session
    return install


def test_get_ok_gui_referer(fake):
    s = fake([200])
    r = http_utils.get("https://mwg.vn/a", referer="https://mwg.vn/list")
    assert r.status_code == 200
    assert s.calls == [("https://mwg.vn/a", {"Referer": "https://mwg.vn/list"})]


def test_403_dung_ngay_khong_retry(fake):
    s = fake([403, 200])
    with pytest.raises(PermissionError):
        http_utils.get("https://mwg.vn/a")
    assert len(s.calls) == 1


def test_5xx_va_429_duoc_retry(fake):
    s = fake([503, 429, 200])
    assert http_utils.get("https://mwg.vn/a").status_code == 200
    assert len(s.calls) == 3


def test_retry_toi_da_4_lan(fake):
    s = fake([500] * 10)
    with pytest.raises(http_utils.Retryable):
        http_utils.get("https://mwg.vn/a")
    assert len(s.calls) == 4


def test_404_khong_retry(fake):
    s = fake([404])
    with pytest.raises(requests.HTTPError):
        http_utils.get("https://mwg.vn/a")
    assert len(s.calls) == 1


def test_ton_trong_robots_txt(fake):
    s = fake([200], robots_txt="User-agent: *\nDisallow: /private/")
    with pytest.raises(http_utils.RobotsDisallowed):
        http_utils.get("https://mwg.vn/private/x.pdf")
    assert s.calls == []
    assert http_utils.get("https://mwg.vn/public/x.pdf").status_code == 200


def test_robots_txt_403_thi_khong_tai_gi(monkeypatch, fake):
    s = fake([403])
    monkeypatch.setattr(http_utils, "_robots", {})
    with pytest.raises(http_utils.RobotsDisallowed):
        http_utils.get("https://mwg.vn/a")
    assert [c[0] for c in s.calls] == ["https://mwg.vn/robots.txt"]


def test_nghi_giua_cac_request(monkeypatch, fake):
    fake([200, 200])
    slept = []
    monkeypatch.setattr(time, "sleep", slept.append)
    http_utils.get("https://mwg.vn/a")
    http_utils.get("https://mwg.vn/b.pdf", kind="pdf")
    assert len(slept) == 1 and 4.5 < slept[0] <= 10.0
