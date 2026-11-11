"""Lấy text và "từ + tọa độ" của từng trang, cho cả PDF có lớp chữ và bản scan/ảnh.

- PDF có lớp chữ (has_text_layer = true): pdfplumber.
- PDF scan, ảnh .jpg/.png: render bằng pypdfium2, OCR bằng Tesseract (vie+eng) qua pytesseract.

Kết quả OCR được cache theo sha256 + trang + dpi trong data/_cache/ocr/<sha256>/,
nên chạy lại không OCR lại. Trên Windows, đặt biến môi trường TESSERACT_CMD nếu
tesseract.exe không nằm trong PATH (xem README).
"""
from __future__ import annotations

import csv
import io
import os
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
import pytesseract
from PIL import Image

from src.common.paths import DATA_DIR

OCR_CACHE = DATA_DIR / "_cache" / "ocr"
OCR_LANG = "vie+eng"
PASS1_DPI = 100
PASS2_DPI = 300
IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
MIN_UPRIGHT_WORDS = 40  # dưới mức này coi như trang bị xoay hoặc gần như trống

if os.environ.get("TESSERACT_CMD"):
    pytesseract.pytesseract.tesseract_cmd = os.environ["TESSERACT_CMD"]

# Một "từ": {"text", "x0", "y0", "x1", "y1"}; tọa độ tính theo điểm ảnh (OCR) hoặc point (pdfplumber).
Word = dict


def page_count(path: Path) -> int:
    if path.suffix.lower() in IMAGE_EXTS:
        return 1
    pdf = pdfium.PdfDocument(str(path))
    try:
        return len(pdf)
    finally:
        pdf.close()


def render(path: Path, page: int, dpi: int) -> Image.Image:
    """Ảnh của trang (đánh số từ 1). File ảnh chỉ có trang 1, giữ nguyên độ phân giải gốc."""
    if path.suffix.lower() in IMAGE_EXTS:
        return Image.open(path).convert("RGB")
    pdf = pdfium.PdfDocument(str(path))
    try:
        return pdf[page - 1].render(scale=dpi / 72).to_pil()
    finally:
        pdf.close()


def _cache_file(sha256: str, page: int, dpi: int, ext: str) -> Path:
    return OCR_CACHE / sha256 / f"p{page:03d}_{dpi}.{ext}"


def _word_score(text: str) -> int:
    """Số từ "thật" (>= 3 chữ cái) - trang bị xoay cho ra toàn ký tự rời rạc."""
    return sum(1 for t in text.split() if len(t) >= 3 and t.isalpha())


def ocr_text(path: Path, sha256: str, page: int, dpi: int = PASS1_DPI) -> str:
    """Pass 1: text thuần của trang, dùng để tìm trang tiêu đề và đọc ngày báo cáo.

    Trang ít chữ đọc được thì thử xoay 90/270/180 độ (BCTC quý có trang in ngang, mỗi trang
    xoay một chiều) và giữ chiều cho nhiều từ nhất; chiều xoay được cache cho pass 2.
    """
    cached = _cache_file(sha256, page, dpi, "txt")
    if cached.exists():
        return cached.read_text(encoding="utf-8")
    img = render(path, page, dpi)
    best_text, best_rot = pytesseract.image_to_string(img, lang=OCR_LANG), 0
    if _word_score(best_text) < MIN_UPRIGHT_WORDS:
        for rot in (90, 270, 180):
            text = pytesseract.image_to_string(img.rotate(rot, expand=True), lang=OCR_LANG)
            if _word_score(text) > _word_score(best_text):
                best_text, best_rot = text, rot
    cached.parent.mkdir(parents=True, exist_ok=True)
    _cache_file(sha256, page, 0, "rot").write_text(str(best_rot), encoding="utf-8")
    cached.write_text(best_text, encoding="utf-8")
    return best_text


def page_rotation(path: Path, sha256: str, page: int) -> int:
    rot = _cache_file(sha256, page, 0, "rot")
    if not rot.exists():
        ocr_text(path, sha256, page)
    return int(rot.read_text(encoding="utf-8"))


def ocr_words(path: Path, sha256: str, page: int, dpi: int = PASS2_DPI) -> list[Word]:
    """Pass 2: từng từ kèm bbox (Tesseract TSV), cache nguyên file TSV."""
    cached = _cache_file(sha256, page, dpi, "tsv")
    if cached.exists():
        tsv = cached.read_text(encoding="utf-8")
    else:
        img = render(path, page, dpi).rotate(page_rotation(path, sha256, page), expand=True)
        # --psm 6: coi trang là một khối văn bản đều -> giữ được các dòng của bảng
        tsv = pytesseract.image_to_data(img, lang=OCR_LANG, config="--psm 6")
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(tsv, encoding="utf-8")
    return parse_tsv(tsv)


def parse_tsv(tsv: str) -> list[Word]:
    words = []
    for r in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        text = (r.get("text") or "").strip()
        if not text or r.get("conf") in (None, "-1"):
            continue
        x0, y0 = int(r["left"]), int(r["top"])
        words.append({"text": text, "x0": x0, "y0": y0,
                      "x1": x0 + int(r["width"]), "y1": y0 + int(r["height"])})
    return words


def text_layer_text(path: Path, page: int) -> str:
    with pdfplumber.open(path) as pdf:
        return pdf.pages[page - 1].extract_text() or ""


def text_layer_words(path: Path, page: int) -> list[Word]:
    with pdfplumber.open(path) as pdf:
        return [{"text": w["text"], "x0": w["x0"], "y0": w["top"], "x1": w["x1"], "y1": w["bottom"]}
                for w in pdf.pages[page - 1].extract_words(keep_blank_chars=False, use_text_flow=False)]


def page_text(path: Path, sha256: str, page: int, text_layer: bool) -> str:
    return text_layer_text(path, page) if text_layer else ocr_text(path, sha256, page)


def page_words(path: Path, sha256: str, page: int, text_layer: bool) -> list[Word]:
    return text_layer_words(path, page) if text_layer else ocr_words(path, sha256, page)
