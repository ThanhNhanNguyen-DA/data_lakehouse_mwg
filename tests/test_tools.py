"""Test cho tools/build_dim_account.py và tools/map_tt99_numeric.py (số giả lập, không phải số liệu MWG)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import build_dim_account as B  # noqa: E402
import map_tt99_numeric as M  # noqa: E402


# ---------- build_dim_account ----------

def test_level_of():
    assert B.level_of("TỔNG CỘNG TÀI SẢN") == 0
    assert B.level_of("A - TÀI SẢN NGẮN HẠN") == 1
    assert B.level_of("II. Đầu tư tài chính ngắn hạn") == 2
    assert B.level_of("1. Tiền") == 3
    assert B.level_of("- Nguyên giá") == 4
    assert B.level_of("a) Cổ phiếu phổ thông") == 4


def test_formulas_in_dau_cong_tru_va_bo_cong_thuc_long_ngoac():
    found = B.formulas_in("Tổng (270 = 100 + 200) ... (10 = 01 - 02) ... {30 = 20 + (21 - 22)}")
    assert found["270"] == [(1, "100"), (1, "200")]
    assert found["10"] == [(1, "01"), (-1, "02")]
    assert "30" not in found


def test_strip_name_bo_so_thu_tu_va_cong_thuc():
    assert B.strip_name("1. Doanh thu thuần (10 = 01 - 02)") == B.strip_name("Doanh thu thuần")
    assert B.strip_name("I. Tiền") == "tien"


def test_layout_parents_theo_cap():
    items = [("B01", "100", "A - TÀI SẢN NGẮN HẠN"), ("B01", "110", "I. Tiền"), ("B01", "111", "1. Tiền mặt"),
             ("B01", "120", "II. Đầu tư"), ("B01", "270", "TỔNG CỘNG TÀI SẢN")]
    lp = B.layout_parents(items)
    assert lp[("B01", "110")] == "100" and lp[("B01", "111")] == "110" and lp[("B01", "120")] == "100"
    assert ("B01", "270") not in lp


def test_build_rows_sign_va_cha():
    """DBG-24: khoản giảm trừ B02 và dòng (*) B01 có sign = -1; 52 không gán sign; mã không chắc bỏ cha."""
    items = [("B01", "130", "III. Phải thu"), ("B01", "137", "7. Dự phòng phải thu khó đòi (*)"),
             ("B01", "131", "1. Phải thu khách hàng"),
             ("B02", "01", "1. Doanh thu"), ("B02", "02", "2. Giảm trừ"), ("B02", "10", "3. Doanh thu thuần"),
             ("B02", "52", "Thuế hoãn lại"), ("B02", "21", "Doanh thu tài chính")]
    formulas = {("B02", "10"): [(1, "01"), (-1, "02")]}
    lp = {("B01", "137"): "130", ("B01", "131"): "130"}
    rows = {(r["statement"], r["account_code"]): r
            for r in B.build_rows("TT200", items, formulas, lp, "src", {("B02", "21"): "không chắc"})}
    assert rows[("B01", "137")]["sign"] == "-1" and rows[("B01", "137")]["is_additive"] == "1"
    assert rows[("B01", "131")]["sign"] == "1"
    assert rows[("B02", "02")]["sign"] == "-1" and rows[("B02", "02")]["parent_code"] == "10"
    assert rows[("B02", "02")]["is_additive"] == "1"
    assert rows[("B02", "52")]["sign"] == ""
    assert rows[("B02", "21")]["parent_code"] == "" and "KHÔNG CHẮC" in rows[("B02", "21")]["source"]
    assert rows[("B01", "130")]["is_subtotal"] == "1" and rows[("B01", "131")]["is_subtotal"] == "0"


# ---------- map_tt99_numeric ----------

def _row(code, canonical="", basis="", note=""):
    return {"regime": "TT99", "statement": "B01", "account_code": code, "canonical_code": canonical,
            "mapping_basis": basis, "semantic_break": "", "source": "", "note": note}


def test_match_numeric_cac_truong_hop():
    level = {("TT99", c): "3" for c in ("161", "110", "130", "140", "150", "120", "170")}
    level |= {("TT200", c): "3" for c in ("151", "110", "130", "140", "141", "150", "120", "170")}
    level[("TT200", "100")] = "1"
    rows = [_row("161"),                       # chưa ghép theo tên, số khớp đúng một mã -> ghép mới
            _row("110", "110"),                # ghép theo tên, số khớp -> name+numeric
            _row("130", "130"),                # ghép theo tên, số lệch hẳn -> semantic_break
            _row("140", "140"),                # ghép theo tên, lệch một chữ số (giống OCR) -> không semantic_break
            _row("150", "150"),                # số khớp mã khác ghép theo tên -> mâu thuẫn
            _row("120"),                       # khớp hai mã cùng cấp -> khớp nhiều mã
            _row("170", "170", basis=M.BASIS)]  # ghép numeric lần trước -> tính lại, nay không khớp
    v99 = {"161": 500, "110": 1_000, "130": 9_999_000, "140": 123_456_789, "150": 777, "120": 42, "170": 31}
    all99 = {k: {v} for k, v in v99.items()}
    v200 = {"151": 500, "110": 1_001, "130": 5_000_000, "140": 123_456_780, "150": 1, "141": 777,
            "120": 42, "170": 42, "100": 31}  # 100 khác cấp với 170: không được ghép
    result = M.match_numeric(rows, v99, all99, v200, level, "2025")
    by = {r["account_code"]: r for r in rows}
    assert by["161"]["canonical_code"] == "151" and by["161"]["mapping_basis"] == M.BASIS
    assert "cuối kỳ BCTC 2025" in by["161"]["source"]  # trước đây biến ref bị ghi đè -> "None"
    assert by["110"]["mapping_basis"] == "name+" + M.BASIS
    assert by["130"]["semantic_break"] == "true"
    assert by["140"]["semantic_break"] == ""
    assert "khớp mã 141" in by["150"]["note"]
    assert [c for c, _, _ in result["khớp nhiều mã"]] == ["120"]
    assert by["170"]["canonical_code"] == "" and by["170"]["mapping_basis"] == ""
    assert [c for c, _, _ in result["semantic_break"]] == ["130"]


def test_match_numeric_bao_cao_khong_thong_nhat_va_bang_0():
    rows = [_row("110", "110"), _row("112", "112")]
    level = {("TT99", "110"): "3", ("TT200", "110"): "3", ("TT99", "112"): "3"}
    result = M.match_numeric(rows, {"110": 10, "112": 0}, {"110": {10, 11}, "112": {0}}, {"110": 10}, level, "2025")
    assert [c for c, _, _ in result["báo cáo 2026 không thống nhất"]] == ["110"]
    assert [c for c, _, _ in result["bằng 0"]] == ["112"]


def test_match_numeric_du_phong_q4_va_bien_dong_20():
    rows = [_row("135", "136"), _row("111", "111")]
    level = {("TT99", "135"): "3", ("TT200", "136"): "3", ("TT99", "111"): "3", ("TT200", "111"): "3"}
    v99 = {"135": 1_778, "111": 1_100}
    # BCTC năm không đọc được ô 136 -> dùng số cùng ngày của BCTC Q4 (chỉ để gắn cờ, không để ghép)
    result = M.match_numeric(rows, v99, {k: {v} for k, v in v99.items()}, {"111": 1_000}, level, "2025",
                             v200_fallback={"136": 2_835})
    by = {r["account_code"]: r for r in rows}
    assert by["135"]["semantic_break"] == "true" and by["135"]["canonical_code"] == "136"
    assert [(c, h[0]) for c, _, h in result[M.SHIFT_KEY]] == [("135", "136")]  # 111: +10% không vào danh sách


def test_dim_account_overrides_chi_ap_dung_dong_da_xac_nhan(tmp_path):
    import csv
    path = tmp_path / "o.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["regime", "statement", "code", "name", "parent_code", "sign", "source_file", "page", "verified_by"])
        w.writerow(["TT99", "B01", "410", "I. Vốn chủ sở hữu", "400", "1", "phu-luc-4-thong-tu-99.pdf", "5", "người dùng"])
        w.writerow(["TT99", "B01", "999", "Mã chưa xác nhận", "400", "1", "phu-luc-4-thong-tu-99.pdf", "5", ""])
    rows = [{"regime": "TT99", "statement": "B01", "account_code": "411", "name_vi": "1. Vốn góp", "name_en": "",
             "level": 3, "parent_code": "410", "is_subtotal": "0", "is_additive": "1", "sign": "1", "source": "x"}]
    assert B.apply_dim_overrides(rows, path) == ["TT99/B01/410"]
    by = {r["account_code"]: r for r in rows}
    assert by["410"]["parent_code"] == "400" and by["410"]["level"] == 2 and by["410"]["is_subtotal"] == "1"
    assert "người dùng" in by["410"]["source"] and "999" not in by  # dòng chưa xác nhận không áp dụng
