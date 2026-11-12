"""Chuẩn hóa chuỗi tiếng Việt. Mọi tầng dùng chung hàm norm() này, không viết lại."""
import unicodedata


def norm(s: str | None) -> str:
    """Bỏ dấu, 'đ' -> 'd', chữ thường, gộp khoảng trắng. None hoặc rỗng -> ''."""
    if not s:
        return ""
    # 'đ'/'Đ' không tách được bằng NFD nên phải thay trước
    s = s.replace("đ", "d").replace("Đ", "D")
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return " ".join(s.lower().split())
