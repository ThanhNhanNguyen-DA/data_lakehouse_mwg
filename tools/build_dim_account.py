"""Dựng config/dim_account.csv và config/account_code_regime_map.csv chỉ từ docs/reference/.

    soffice --headless --convert-to "txt:Text (encoded):UTF8" --outdir /tmp/ref docs/reference/*.doc
    python tools/build_dim_account.py /tmp/ref config

TT200/TT202 đọc từ text chuyển từ file .doc; TT99 (PDF scan) lấy từ tools/tt99_forms.py (chép tay từ bản scan,
công thức lấy từ phần hướng dẫn lập). Mã không chắc: parent_code để trống, ghi lý do ở cột source.

config/dim_account_overrides.csv (regime, statement, code, name, parent_code, sign, source_file, page, verified_by):
dòng người dùng tự đọc trên văn bản gốc (vd. mã bộ đọc bỏ sót trên bản scan), chỉ áp dụng khi có source_file và
verified_by; thêm mã mới hoặc thay tên/mã cha/dấu của mã có sẵn, áp dụng trước khi dựng bảng map TT99 -> TT200.
Sau khi dựng lại thì chạy tools/map_tt99_numeric.py --ref 2025 (bảng map được ghi lại từ đầu).
"""
import csv, re, sys
from collections import Counter, defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.common.text import norm
sys.path.insert(0, str(Path(__file__).parent))
import tt99_forms as T

SRC_202 = "TT202/2014 PL1 (TVT-ProTrain-PL01-BCTCHN.doc)"
SRC_200 = "TT200/2014 PL2 (phu-luc-2.doc)"
SRC_99 = "TT99/2025 PL IV (phu-luc-4-thong-tu-99.pdf)"

def level_of(name):
    n = name.strip()
    if re.match(r"^(TỔNG CỘNG)", n): return 0
    if re.match(r"^[A-D]\s*[-–]", n): return 1
    if re.match(r"^[IVX]+\.", n): return 2
    if re.match(r"^\d+\.", n): return 3
    if re.match(r"^[a-z]\)", n): return 4
    if re.match(r"^-", n): return 4
    return 3

B03_MARK, B03_END = "(Theo phương pháp gián tiếp)", "Ghi chú:"  # MWG lập B03 theo phương pháp gián tiếp


def parse_202(text, markers=None, stop="Mẫu số B 03"):
    """(statement, code, name) theo thứ tự trong mẫu B01-DN/HN và B02-DN/HN của TT202 (markers: dòng mở đầu -> báo cáo)."""
    markers = markers or {"Mẫu số B 01 - DN/HN": "B01", "Mẫu số B 02 - DN/HN": "B02"}
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    out, st, name = [], None, []
    for l in lines:
        hit = next((v for k, v in markers.items() if k in l), None)
        if hit: st = hit; continue
        if stop in l: break
        if st is None: continue
        if re.fullmatch(r"\d{2,3}[ab]?", l):
            if name: out.append((st, l, " ".join(name))); name = []
            continue
        if re.fullmatch(r"[\d ¦]+|\(\s*\.+\s*\)|\{.*\}", l) or l in ("TÀI SẢN", "Mã số", "Thuyết minh", "CHỈ TIÊU", "Năm nay", "Năm trước") \
                or l.startswith(("Số cuối", "Số đầu", "Đơn vị", "Địa chỉ", "(Ban hành", "BẢNG", "BÁO CÁO", "Tại ngày", "Năm")):
            continue
        name = [l] if re.match(r"^([A-D]\s*[-–]|[IVX]+\.|\d+\.|-|TỔNG)", l) else name + [l]
    return out

def b03_section(text):
    """Mẫu B03-DN/HN theo phương pháp gián tiếp trong TT202 PL1 (sau mẫu trực tiếp), tới dòng "Ghi chú:"."""
    start = text.index(B03_MARK)
    return text[start:text.index(B03_END, start)]


def formulas_in(text):
    """Công thức dạng (X = Y + Z - W) in trên mẫu."""
    found = {}
    for m in re.finditer(r"[({]\s*(\d{2,3})\s*=\s*([0-9 +\-–()]+?)\s*[)}]", text):
        lhs, rhs = m.group(1), m.group(2)
        if "(" in rhs: continue  # công thức có ngoặc lồng (mã 30): xử lý riêng
        found.setdefault(lhs, [(-1 if s and s in "-–" else 1, c) for s, c in re.findall(r"([+\-–]?)\s*(\d{2,3})", rhs)])
    return found

def strip_name(name):
    n = re.sub(r"\s*[({]\s*\d{2,3}\s*=.*$", "", name)          # bỏ công thức in kèm tên
    n = re.sub(r"\(\*\)", "", n)
    n = re.sub(r"^([A-D]\s*[-–]|[IVX]+\.|\d+\.|[a-z]\)|-)\s*", "", n.strip())
    n = re.sub(r"^Trong đó:\s*", "", n)
    return norm(n)

def build_rows(regime, items, formulas, layout_parents, source, uncertain):
    rows, parent_of, op_of = [], {}, {}
    for (st, lhs), terms in formulas.items():
        for op, c in terms:
            parent_of[(st, c)], op_of[(st, c)] = lhs, op
    codes = {(st, c) for st, c, _ in items}
    for st, code, name in items:
        key = (st, code)
        star = "(*)" in name and st == "B01"  # B02: (*) = "chỉ áp dụng tại công ty cổ phần", không phải số âm
        parent, src_p = parent_of.get(key), "công thức"
        if parent is None and key in layout_parents:
            parent, src_p = layout_parents[key], "bố cục mẫu (I./1./-)"
        is_trong_do = "trong đó" in norm(name)
        if key in uncertain:
            parent, src_p = None, ""
        if is_trong_do:
            additive = "0"
        elif parent is None:
            additive = ""
        elif op_of.get(key, 1) == -1 and not star and key not in DEDUCTIONS[regime]:
            additive = ""  # vế trừ mà không chắc là khoản giảm trừ (vd. 52): bỏ qua khi cộng dòng con
        else:
            additive = "1"
        sign = "-1" if star or key in DEDUCTIONS[regime] else ("" if key in NO_SIGN[regime] else "1")
        rows.append({"regime": regime, "statement": st, "account_code": code, "name_vi": name.strip(), "name_en": "",
                     "level": level_of(name), "parent_code": parent or "", "is_subtotal": "",
                     "is_additive": additive, "sign": sign,
                     "source": source + (f"; cha: {src_p}" if parent else "") + ("; KHÔNG CHẮC: " + uncertain[key] if key in uncertain else "")})
    has_child = {(r["statement"], r["parent_code"]) for r in rows if r["parent_code"]}
    for r in rows:
        r["is_subtotal"] = "1" if (r["statement"], r["account_code"]) in has_child else "0"
    return rows

def layout_parents(items):
    out, stack = {}, {}
    for st, code, name in items:
        if st != "B01": continue
        lv = level_of(name)
        if lv == 0: stack = {}; continue
        parent = next((stack[l] for l in range(lv - 1, 0, -1) if l in stack), None)
        if parent: out[(st, code)] = parent
        stack = {k: v for k, v in stack.items() if k < lv}; stack[lv] = code
    return out

# Khoản giảm trừ ở B02 (vế trừ trong công thức in trên mẫu, luôn là chi phí/giảm trừ):
DEDUCTIONS = {"TT200": {("B02", c) for c in ("02", "11", "22", "23", "25", "26", "32", "51")},
              "TT99": {("B02", c) for c in ("02", "11", "23", "24", "25", "26", "32", "51")}}
# Có thể âm hoặc dương ("Chi phí thuế TNDN hoãn lại" có thể là thu nhập): không gán sign
NO_SIGN = {"TT200": {("B02", "52")}, "TT99": {("B02", "52")}}

OVERRIDES = Path(__file__).resolve().parents[1] / "config" / "dim_account_overrides.csv"


def apply_dim_overrides(rows: list[dict], path: Path = OVERRIDES) -> list[str]:
    """Áp dụng dim_account_overrides.csv vào rows (sửa tại chỗ). Trả danh sách mã đã áp dụng."""
    if not Path(path).exists():
        return []
    applied = []
    with Path(path).open(encoding="utf-8", newline="") as fh:
        for o in csv.DictReader(fh):
            if not (o.get("code", "").strip() and o.get("source_file", "").strip() and o.get("verified_by", "").strip()):
                continue  # chưa có nguồn hoặc chưa người xác nhận: không dùng
            key = (o["regime"].strip(), o["statement"].strip(), o["code"].strip())
            src = f"override: {o['source_file'].strip()} tr. {o.get('page', '').strip()}, xác nhận: {o['verified_by'].strip()}"
            row = next((r for r in rows if (r["regime"], r["statement"], r["account_code"]) == key), None)
            if row is None:
                row = {"regime": key[0], "statement": key[1], "account_code": key[2], "name_vi": "", "name_en": "",
                       "level": "", "parent_code": "", "is_subtotal": "", "is_additive": "", "sign": "", "source": ""}
                rows.append(row)
            if o.get("name", "").strip():
                row["name_vi"], row["level"] = o["name"].strip(), level_of(o["name"])
            if o.get("parent_code", "").strip():
                row["parent_code"] = o["parent_code"].strip()
                row["is_additive"] = row["is_additive"] or "1"
            if o.get("sign", "").strip() in ("1", "-1"):
                row["sign"] = o["sign"].strip()
            row["source"] = src
            applied.append("/".join(key))
    for regime in {o.split("/")[0] for o in applied}:  # mã cha mới có thể đổi is_subtotal (chỉ regime có override)
        has_child = {(r["statement"], r["parent_code"]) for r in rows if r["regime"] == regime and r["parent_code"]}
        for r in rows:
            if r["regime"] == regime:
                r["is_subtotal"] = "1" if (r["statement"], r["account_code"]) in has_child else "0"
    return applied


def main(ref: Path, out: Path) -> None:
    """ref: thư mục text đã chuyển từ .doc; out: thư mục config."""
    REF, OUT = Path(ref), Path(out)
    t202 = (REF / "TVT-ProTrain-PL01-BCTCHN.txt").read_text(encoding="utf-8")
    t200 = (REF / "phu-luc-2.txt").read_text(encoding="utf-8")
    items200 = parse_202(t202)
    # B01: công thức cấp mục in trên mẫu TT200 PL2 (100, 200, 300, 400) + TT202 (270, 440); B02: TT202
    f = {}
    for lhs, terms in formulas_in(t200).items():
        if int(lhs) >= 100: f[("B01", lhs)] = terms
    for lhs, terms in formulas_in(t202).items():
        f[("B01" if int(lhs) >= 100 else "B02", lhs)] = terms
    f.pop(("B02", "70"), None)  # "(70 = 50+60+61...)" thuộc B03 (lưu chuyển tiền), không phải B02
    # B03 (gián tiếp): mã và công thức in trên mẫu (50 = 20+30+40, 70 = 50+60+61); số giữ dấu in (sign = 1)
    sec03 = b03_section(t202)
    items200 += parse_202(sec03, markers={B03_MARK: "B03"}, stop=B03_END)
    f.update({("B03", lhs): terms for lhs, terms in formulas_in(sec03).items()})
    unc200 = {("B01", "230"): "công thức in trên mẫu TT200 PL2 '200 = 210 + 220 + 240 + 250 + 260' không có 230, bố cục mẫu thì 230 thuộc mục B",
              **{("B02", c): "công thức mã 30 trong bản TT202 này '{30 = 20 + (21 - 22) - (24 + 25)}' không khớp cấu trúc (24 là lãi/lỗ liên doanh, 25-26 là chi phí)" for c in ("20", "21", "22", "24", "25", "26")}}
    lp = layout_parents(items200)
    rows = build_rows("TT200", items200, f, lp, SRC_202, unc200)

    items99 = [("B01", *l.split("|", 1)) for l in T.B01.splitlines()] + [("B02", *l.split("|", 1)) for l in T.B02.splitlines()]
    f99 = {("B01", lhs): [(1, c) for c in rhs.split("+")] for lhs, rhs in (l.split("=") for l in T.B01_FORMULAS.splitlines())}
    f99.update({("B02", "10"): [(1, "01"), (-1, "02")], ("B02", "20"): [(1, "10"), (-1, "11")],
                ("B02", "40"): [(1, "31"), (-1, "32")], ("B02", "50"): [(1, "30"), (1, "40")],
                ("B02", "60"): [(1, "50"), (-1, "51"), (-1, "52")], ("B02", "23"): [(1, "24")]})
    unc99 = {**{("B01", c): "mẫu hợp nhất của MWG có thêm 429 (lợi ích cổ đông không kiểm soát) trong vốn chủ sở hữu, không có trong mẫu TT99 riêng lẻ: tổng 400 theo mẫu này sẽ thiếu 429" for c in ("411", "412", "413", "414", "415", "416", "417", "418", "419", "420")},
             **{("B02", c): "mẫu hợp nhất của MWG có thêm 27 (phần lãi/lỗ liên doanh): công thức 30 của mẫu TT99 riêng lẻ không áp dụng được" for c in ("20", "21", "22", "23", "25", "26")}}
    rows += build_rows("TT99", items99, f99, {}, SRC_99, unc99)
    for r in rows:  # "Trong đó: Chi phí đi vay/lãi vay" là chi tiết của chi phí tài chính, không cộng vào
        if r["regime"] == "TT99" and r["statement"] == "B02" and r["account_code"] == "24":
            r["parent_code"], r["is_additive"] = "23", "0"
        if r["regime"] == "TT200" and r["statement"] == "B02" and r["account_code"] == "23":
            r["parent_code"], r["is_additive"] = "22", "0"

    applied = apply_dim_overrides(rows)
    if applied:
        print("override:", applied)

    fields = ["regime", "statement", "account_code", "name_vi", "name_en", "level", "parent_code", "is_subtotal",
              "is_additive", "sign", "source"]
    with (OUT / "dim_account.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields); w.writeheader(); w.writerows(rows)

    # ---- map TT99 -> TT200: tên trùng khớp sau chuẩn hóa; tên lặp (Nguyên giá...) thì cần cha cũng khớp ----
    by_name = defaultdict(list)
    for r in rows:
        if r["regime"] == "TT200": by_name[(r["statement"], strip_name(r["name_vi"]))].append(r)
    mp, unmatched = {}, []
    r99 = [r for r in rows if r["regime"] == "TT99"]
    for _ in range(2):
        for r in r99:
            k = (r["statement"], r["account_code"])
            if k in mp: continue
            cands = by_name.get((r["statement"], strip_name(r["name_vi"])), [])
            if len(cands) > 1 and r["parent_code"]:
                pm = mp.get((r["statement"], r["parent_code"]))
                cands = [c for c in cands if c["parent_code"] == pm]
            if len(cands) == 1: mp[k] = cands[0]["account_code"]
    for r in r99:
        if (r["statement"], r["account_code"]) not in mp: unmatched.append(r)
    with (OUT / "account_code_regime_map.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["regime", "statement", "account_code", "canonical_code", "source", "note"])
        w.writeheader()
        for r in r99:
            k = (r["statement"], r["account_code"])
            w.writerow({"regime": "TT99", "statement": r["statement"], "account_code": r["account_code"],
                        "canonical_code": mp.get(k, ""),
                        "source": "tên chỉ tiêu trùng khớp giữa " + SRC_99 + " và " + SRC_202 if k in mp else "",
                        "note": "" if k in mp else "không có chỉ tiêu cùng tên trong mẫu TT200/TT202: chưa map"})
        for st, c, n in (("B01", "429", "Lợi ích cổ đông không kiểm soát"), ("B02", "27", "Phần lãi/lỗ trong công ty liên doanh"),
                         ("B02", "61", "Lợi nhuận sau thuế của công ty mẹ"), ("B02", "62", "Lợi nhuận sau thuế của cổ đông không kiểm soát")):
            w.writerow({"regime": "TT99", "statement": st, "account_code": c, "canonical_code": "", "source": "",
                        "note": f"MWG in mã này trên mẫu B01a/B02a-DN/HN ({n}) nhưng mẫu TT99 PL IV không có: chưa map"})
    print("dim rows:", Counter(r["regime"] for r in rows), "| map:", len(mp), "khớp,", len(unmatched), "không khớp")
    for r in unmatched: print("  ", r["statement"], r["account_code"], r["name_vi"])
    print("uncertain:", sum("KHÔNG CHẮC" in r["source"] for r in rows), "| sign trống:", [(r["regime"], r["statement"], r["account_code"]) for r in rows if not r["sign"]])
    print("TT200 B01 codes:", len([r for r in rows if r["regime"] == "TT200" and r["statement"] == "B01"]), "B02:", len([r for r in rows if r["regime"] == "TT200" and r["statement"] == "B02"]))


if __name__ == "__main__":
    from src.common.lock import run_locked
    sys.exit(run_locked(main, Path(sys.argv[1]), Path(sys.argv[2])))
