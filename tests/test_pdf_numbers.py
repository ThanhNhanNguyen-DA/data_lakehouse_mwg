import pytest

from src.extraction.pdf_extractor import to_number, unit_multiplier


@pytest.mark.parametrize("raw, expected", [
    ("1.234.567", 1234567.0),
    ("123", 123.0),
    ("(1.234)", -1234.0),
    ("( 1.234.567 )", -1234567.0),
    ("-1.234", -1234.0),
    ("−1.234", -1234.0),
    ("12,5", 12.5),
    ("1.234,56", 1234.56),
    ("1 234 567", 1234567.0),
    ("0", 0.0),
])
def test_to_number_kieu_viet_nam(raw, expected):
    assert to_number(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "  ", "-", "–", "()", "(-)"])
def test_to_number_rong_la_null(raw):
    assert to_number(raw) is None


def test_to_number_kieu_tieng_anh():
    assert to_number("1,234,567", thousands=",", decimal=".") == 1234567.0
    assert to_number("(1,234.5)", thousands=",", decimal=".") == -1234.5


@pytest.mark.parametrize("raw", ["1.23.4", "abc", "1.2345", "12a"])
def test_to_number_sai_dinh_dang_bao_loi(raw):
    with pytest.raises(ValueError):
        to_number(raw)


@pytest.mark.parametrize("text, expected", [
    ("Mẫu số B 01 - DN/HN\nĐơn vị tính: VND", 1),
    ("Đơn vị tính: triệu đồng", 1_000_000),
    ("Đơn vị: Nghìn đồng", 1_000),
    ("Đơn vị tính : tỷ đồng", 1_000_000_000),
    ("Unit: VND million", 1_000_000),
    ("Unit: VND", 1),
    ("Đơn vị tính: VND   Công ty CP Đầu tư Thế Giới Di Động", 1),
    ("Bảng cân đối kế toán hợp nhất", None),
])
def test_unit_multiplier(text, expected):
    assert unit_multiplier(text) == expected


# ---------- has_text_layer: PDF tối giản tự tạo (chữ giả, không phải số liệu) ----------

def _make_pdf(text: str | None) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode() if text else b""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


def test_has_text_layer(tmp_path):
    from src.extraction.pdf_extractor import has_text_layer

    text_pdf, scan_pdf, broken = tmp_path / "text.pdf", tmp_path / "scan.pdf", tmp_path / "broken.pdf"
    text_pdf.write_bytes(_make_pdf("BANG CAN DOI KE TOAN HOP NHAT - day la chu gia de kiem tra lop text"))
    scan_pdf.write_bytes(_make_pdf(None))
    broken.write_bytes(b"%PDF-1.4 hong")
    assert has_text_layer(text_pdf) is True
    assert has_text_layer(scan_pdf) is False
    assert has_text_layer(broken) is None
