"""Chụp ảnh từng trang dashboard (Streamlit đang chạy) bằng Playwright headless -> docs/screenshots/.

    streamlit run dashboard/app.py --server.headless true --server.port 8599 &
    python tools/screenshot_dashboard.py --url http://localhost:8599
    # WSL thiếu libasound2 mà không có sudo: apt-get download libasound2t64, dpkg-deb -x vào một thư mục,
    # rồi đặt LD_LIBRARY_PATH=<thư mục>/usr/lib/x86_64-linux-gnu

Mỗi trang: chọn trong sidebar, chờ hết spinner, chụp toàn trang; trang có lỗi (st.exception) thì báo và trả mã 1.
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parents[1] / "docs" / "screenshots"
PAGES = {"Tổng quan": "tong_quan", "Kết quả kinh doanh": "ket_qua_kinh_doanh", "Biên lợi nhuận": "bien_loi_nhuan",
         "Cơ cấu tài sản – nguồn vốn": "co_cau_tai_san", "Độ phủ dữ liệu": "do_phu", "Lineage": "lineage"}


def main(argv: list[str]) -> int:
    url = argv[argv.index("--url") + 1] if "--url" in argv else "http://localhost:8501"
    OUT.mkdir(parents=True, exist_ok=True)
    errors = []
    with sync_playwright() as p:
        page = p.chromium.launch().new_page(viewport={"width": 1440, "height": 1000})
        page.goto(url)
        page.wait_for_selector("section[data-testid='stSidebar']", timeout=60000)
        for i, (name, slug) in enumerate(PAGES.items(), 1):
            page.locator("section[data-testid='stSidebar'] label").filter(has_text=name).first.click()
            page.wait_for_timeout(1500)
            page.wait_for_selector("[data-testid='stStatusWidget']", state="detached", timeout=60000)
            page.wait_for_timeout(2500)  # Plotly vẽ xong
            if page.locator("[data-testid='stException']").count():
                errors.append(name)
            path = OUT / f"{i:02d}_{slug}.png"
            page.screenshot(path=str(path), full_page=True)
            print(f"[dashboard] {name} -> {path.relative_to(OUT.parents[1])}")
    if errors:
        print(f"[dashboard] Trang có lỗi: {', '.join(errors)}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
