"""Lấy text và "từ + tọa độ" của từng trang, cho cả PDF có lớp chữ và bản scan/ảnh.

- PDF có lớp chữ (has_text_layer = true): pdfplumber.
- PDF scan, ảnh .jpg/.png: render bằng pypdfium2, OCR bằng Tesseract (vie+eng) qua pytesseract.

Kết quả OCR được cache theo sha256 + trang + dpi trong data/_cache/ocr/<sha256>/,
nên chạy lại không OCR lại. Trên Windows, đặt biến môi trường TESSERACT_CMD nếu
tesseract.exe không nằm trong PATH (xem README).
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
import pytesseract
import numpy as np
from PIL import Image, ImageOps

from src.common.paths import DATA_DIR

OCR_CACHE = DATA_DIR / "_cache" / "ocr"
OCR_LANG = "vie+eng"
PASS1_DPI = 100
PASS2_DPI = 300
IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
OSD_DPI = 150
CROP_BORDER = 20
NUMBER_LANG = "eng"
NUMBER_CONFIG = "--psm 6 -c tessedit_char_whitelist=0123456789.,()-"
CODE_CONFIG = "--psm 6 -c tessedit_char_whitelist=0123456789ab"  # mã số: 270, 421a, 01
MIN_OSD_CONF = 2.0

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


def detect_rotation(path: Path, page: int) -> int:
    """Góc xoay (độ, ngược chiều kim đồng hồ cho PIL) để trang đứng thẳng, theo OSD của Tesseract.

    BCTC quý có trang B02 in ngang, mỗi trang xoay một chiều. OSD ở 150 dpi đủ tin cậy
    (đo trên PDF thật); độ tin cậy thấp hoặc trang trống thì giữ nguyên.
    """
    try:
        osd = pytesseract.image_to_osd(render(path, page, OSD_DPI), output_type=pytesseract.Output.DICT)
    except pytesseract.TesseractError:
        return 0
    return -int(osd["rotate"]) % 360 if osd["orientation_conf"] >= MIN_OSD_CONF else 0


def ocr_text(path: Path, sha256: str, page: int, dpi: int = PASS1_DPI) -> str:
    """Pass 1: text thuần của trang (đã xoay đúng chiều), để tìm trang tiêu đề và đọc ngày báo cáo.

    Chiều xoay được cache cho pass 2.
    """
    cached = _cache_file(sha256, page, dpi, "txt")
    if cached.exists():
        return cached.read_text(encoding="utf-8")
    rot = detect_rotation(path, page)
    text = pytesseract.image_to_string(render(path, page, dpi).rotate(rot, expand=True), lang=OCR_LANG)
    cached.parent.mkdir(parents=True, exist_ok=True)
    _cache_file(sha256, page, 0, "rot").write_text(str(rot), encoding="utf-8")
    cached.write_text(text, encoding="utf-8")
    return text


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


def _max_runs(mask: np.ndarray) -> np.ndarray:
    """Độ dài đoạn True liền mạch dài nhất theo từng cột của mảng 2 chiều."""
    pad = np.zeros((1, mask.shape[1]), dtype=np.int8)
    d = np.diff(np.vstack([pad, mask.astype(np.int8), pad]), axis=0)
    out = np.zeros(mask.shape[1], dtype=int)
    for c in range(mask.shape[1]):
        starts, ends = np.where(d[:, c] == 1)[0], np.where(d[:, c] == -1)[0]
        if len(starts):
            out[c] = (ends - starts).max()
    return out


def clean_rules(img: Image.Image) -> Image.Image:
    """Xóa đường kẻ bảng rồi thêm lề trắng trước khi OCR một cột.

    Đường kẻ = đoạn đen liền mạch dài (dọc >= 50% chiều cao, ngang >= 80% bề ngang). Nét chữ số
    thẳng hàng giữa các dòng (số căn phải) bị ngắt bởi khoảng trống giữa dòng nên không bị xóa.
    Đường kẻ dính trong vùng cắt làm Tesseract bỏ sót chữ, vd. mã 270 trong ô đóng khung cuối B01.
    """
    a = np.array(img.convert("L"))
    dark = a < 160
    for c in np.where(_max_runs(dark) >= 0.5 * a.shape[0])[0]:
        a[:, max(c - 2, 0):c + 3] = 255
    for r in np.where(_max_runs(dark.T) >= 0.8 * a.shape[1])[0]:
        a[max(r - 2, 0):r + 3, :] = 255
    return ImageOps.expand(Image.fromarray(a), border=CROP_BORDER, fill=255)


def ocr_columns(path: Path, sha256: str, page: int, boxes: list[tuple[int, int, int, int]],
                dpi: int = PASS2_DPI, config: str | None = None) -> list[list[Word]]:
    """OCR riêng từng cột số tiền (crop theo box trên ảnh đã xoay, cùng hệ tọa độ với ocr_words).

    Chỉ cho phép chữ số và . , ( ) - nên không còn lỗi kiểu '1.234.5B7'; tọa độ trả về theo trang.
    """
    out, img = [], None
    for x0, y0, x1, y1 in boxes:
        config = config or NUMBER_CONFIG
        tag = hashlib.md5(config.encode()).hexdigest()[:6]
        cached = OCR_CACHE / sha256 / f"p{page:03d}_{dpi}_col_{x0}_{y0}_{x1}_{y1}_{tag}.tsv"
        if cached.exists():
            tsv = cached.read_text(encoding="utf-8")
        else:
            if img is None:
                img = render(path, page, dpi).rotate(page_rotation(path, sha256, page), expand=True)
            tsv = pytesseract.image_to_data(clean_rules(img.crop((x0, y0, x1, y1))), lang=NUMBER_LANG, config=config)
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(tsv, encoding="utf-8")
        dx, dy = x0 - CROP_BORDER, y0 - CROP_BORDER
        out.append([{**w, "x0": w["x0"] + dx, "x1": w["x1"] + dx, "y0": w["y0"] + dy, "y1": w["y1"] + dy}
                    for w in parse_tsv(tsv)])
    return out


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
