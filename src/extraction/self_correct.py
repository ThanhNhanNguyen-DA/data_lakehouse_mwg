"""Tự sửa lỗi OCR sau khi bóc: bỏ phiếu giữa các lần in lại một số, gợi ý sửa ô theo phương trình.

Chỉ sửa khi khác biệt trông giống lỗi OCR (cùng số chữ số, sai <= 2 chữ số, hoặc chỉ mất dấu âm),
để không ghi đè số trình bày lại (restatement) - số gốc "as reported" vẫn thắng.
"""
from __future__ import annotations

import csv
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from src.extraction.pdf_extractor import _contribution

OVERRIDE_FIELDS = ["symbol", "statement", "fiscal_year", "fiscal_quarter", "period_kind", "account_code",
                   "amount_vnd", "ocr_value", "source_doc_sha256", "page", "reason", "entered_by", "entered_at",
                   "suggested_by", "status"]


def ocr_like(a: int, b: int) -> bool:
    """a và b khác nhau kiểu lỗi OCR: cùng số chữ số và sai <= 2 vị trí, hoặc chỉ khác dấu (mất ngoặc)."""
    sa, sb = str(abs(a)), str(abs(b))
    if len(sa) != len(sb):
        return False
    diff = sum(x != y for x, y in zip(sa, sb))
    return diff == 0 or (diff <= 2 and (a < 0) == (b < 0))


def _year_end_of(doc: dict, kind: str) -> int | None:
    """Ngày 31/12 nào mà cột B01 này trình bày: cuối kỳ của BCTC năm/Q4 N -> N; đầu năm của mọi báo cáo năm N -> N-1."""
    year = int(doc["period"][:4]) if doc["period"][:4].isdigit() else None
    if year is None:
        return None
    if kind == "closing" and (doc["doc_type"] == "FS_CONSOLIDATED_Y" or doc["period"].endswith("Q4")):
        return year
    if kind == "opening":
        return year - 1
    return None


def apply_consensus(docs: list[dict]) -> int:
    """Bỏ phiếu đa số giữa các lần một số B01 ngày 31/12/N được in (cuối kỳ BCTC năm/Q4 N, đầu năm các báo cáo N+1).

    >= 2 nguồn khớp và hơn hẳn các giá trị khác -> giá trị đồng thuận; các ô lệch kiểu lỗi OCR được sửa theo,
    ô trống/thiếu chỉ được điền khi mọi nguồn đọc được đều khớp nhau. Trả về số ô đã sửa/điền.
    """
    groups: dict[tuple[int, str], list[dict]] = defaultdict(list)
    members: dict[tuple[int, str], list[dict]] = defaultdict(list)  # (năm, kỳ cột) -> các báo cáo có cột đó
    for d in docs:
        kinds = {c["period_kind"] for c in d["cells"] if c["statement"] == "B01"}
        for kind in kinds & {"closing", "opening"}:
            ye = _year_end_of(d, kind)
            if ye is not None:
                members[(ye, kind)].append(d)
        for c in d["cells"]:
            ye = _year_end_of(d, c["period_kind"]) if c["statement"] == "B01" else None
            if ye is not None and c.get("canonical_code") and not c.get("semantic_break"):
                groups[(ye, c["canonical_code"])].append(c)
    fixed = 0
    for (ye, code), cells in groups.items():
        votes = Counter(c["amount_vnd"] for c in cells if c["amount_vnd"] is not None)
        if not votes:
            continue
        (top, n), *rest = votes.most_common()
        if n < 2 or (rest and rest[0][1] == n):
            continue
        for c in cells:
            v = c["amount_vnd"]
            if v == top:
                c["is_consensus"] = True
            elif (v is None and len(votes) == 1) or (v is not None and ocr_like(v, top)):
                c.update(amount_vnd=top, is_consensus=True, corrected_by="consensus", ocr_value=v,
                         value_origin="consensus_fixed" if v is not None else "consensus_filled")
                fixed += 1
        if len(votes) == 1:  # mọi nguồn khớp: điền ô OCR làm mất ở các báo cáo còn lại
            template = cells[0]
            for kind in ("closing", "opening"):
                for d in members.get((ye, kind), []):
                    if d.get("regime", "TT200") != "TT200":
                        continue  # chỉ báo cáo TT200 có mã in = mã chuẩn; TT99 cùng số mã có thể là chỉ tiêu khác
                    if not any(c["statement"] == "B01" and c.get("canonical_code") == code and c["period_kind"] == kind
                               for c in d["cells"]):
                        d["cells"].append({**template, "page": None, "period_kind": kind, "raw_text": "",
                                           "account_code": code, "source_doc_sha256": d["sha256"],
                                           "is_consensus": True, "corrected_by": "consensus", "ocr_value": None,
                                           "is_manual": False, "value_origin": "consensus_filled"})
                        fixed += 1
    return fixed


def _verified(doc: dict) -> set[tuple[str, str, str]]:
    """Ô đã được xác nhận: khớp khi bỏ phiếu giữa các lần in lại, hoặc nằm trong một phương trình đúng."""
    ok = {(c["statement"], c["account_code"], c["period_kind"]) for c in doc.get("cells", []) if c.get("is_consensus")}
    for chk in doc.get("checks", []):
        eq = chk.get("equation")
        if chk["ok"] and eq:
            ok |= {(eq["statement"], code, chk["kind"]) for code in [eq["lhs"]] + [c for _, c in eq["terms"]]}
    return ok


def suggest_fixes(doc: dict, failed: list[dict], values: dict, signs: dict) -> list[dict]:
    """Phương trình sai mà chỉ đúng một ô có thể sửa kiểu lỗi OCR -> gợi ý giá trị cho ô đó (chưa áp dụng).

    Sai một chữ số ở một vế thường làm tổng cũng lệch một chữ số, nên vế trái và vế sai đều "giống lỗi OCR":
    loại các ô đã xác nhận (bỏ phiếu, phương trình khác đúng); còn hơn một ứng viên thì không gợi ý.
    """
    verified, out = _verified(doc), []
    for f in failed:
        eq, kind = f["equation"], f["kind"]
        st = eq["statement"]
        left = values.get((st, eq["lhs"], kind))
        parts = [values.get((st, code, kind)) for _, code in eq["terms"]]
        if left is None or None in parts:
            continue
        contribs = [_contribution(op, v, signs.get((st, code))) for (op, code), v in zip(eq["terms"], parts)]
        cands = []
        right = sum(contribs)
        if ocr_like(left, right):
            cands.append((eq["lhs"], left, right))
        for (op, code), v, c in zip(eq["terms"], parts, contribs):
            need = left - (right - c)  # đóng góp cần có của ô này
            if signs.get((st, code)) == -1:
                if need > 0:
                    continue
                want = abs(need) * (-1 if v < 0 else 1)  # giữ dấu in trên báo cáo
            else:
                want = need * op
            if ocr_like(v, want):
                cands.append((code, v, want))
        cands = [c for c in cands if (st, c[0], kind) not in verified]
        if len(cands) == 1:
            code, ocr_value, want = cands[0]
            out.append({"statement": st, "account_code": code, "period_kind": kind, "amount_vnd": want,
                        "ocr_value": ocr_value, "reason": f"Phương trình {eq['label']}: chỉ ô này sai"})
    return out


def write_suggestions(path: Path, doc: dict, suggestions: list[dict], suggested_by: str = "equation") -> int:
    """Thêm gợi ý vào manual_overrides.csv với suggested_by (mặc định equation), status=pending (bỏ qua ô đã có dòng)."""
    rows = []
    if Path(path).exists():
        with Path(path).open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
    have = {(r["source_doc_sha256"], r["statement"], r["account_code"], r["period_kind"]) for r in rows}
    period = doc["period"]
    quarter = {"H1": "H1"}.get(period[5:], period[6:] if "-Q" in period else "0")
    added = 0
    for s in suggestions:
        key = (doc["sha256"], s["statement"], s["account_code"], s["period_kind"])
        if key in have:
            continue
        page = next((c["page"] for c in doc["cells"] if (c["statement"], c["account_code"], c["period_kind"])
                     == key[1:]), "")
        rows.append({"symbol": "MWG", "statement": s["statement"], "fiscal_year": period[:4], "fiscal_quarter": quarter,
                     "period_kind": s["period_kind"], "account_code": s["account_code"],
                     "amount_vnd": s["amount_vnd"], "ocr_value": s["ocr_value"], "source_doc_sha256": doc["sha256"],
                     "page": page, "reason": s["reason"], "entered_by": "", "entered_at": date.today().isoformat(),
                     "suggested_by": suggested_by, "status": "pending"})
        have.add(key)
        added += 1
    with Path(path).open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OVERRIDE_FIELDS)
        w.writeheader()
        w.writerows({k: r.get(k, "") for k in OVERRIDE_FIELDS} for r in rows)
    return added


def prune_suggestions(path: Path, suggested_by: str, keep: set[tuple[str, str, str, str]]) -> int:
    """Xóa dòng pending do `suggested_by` sinh ra mà lượt này không sinh lại (vấn đề đã tự phân xử được).
    Dòng người dùng đã đổi status (confirmed, rejected...) giữ nguyên. Trả số dòng đã xóa."""
    if not Path(path).exists():
        return 0
    with Path(path).open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    out = [r for r in rows if not (r.get("suggested_by") == suggested_by and (r.get("status") or "") == "pending"
                                   and (r["source_doc_sha256"], r["statement"], r["account_code"], r["period_kind"]) not in keep)]
    if len(out) != len(rows):
        with Path(path).open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=OVERRIDE_FIELDS)
            w.writeheader()
            w.writerows({k: r.get(k, "") for k in OVERRIDE_FIELDS} for r in out)
    return len(rows) - len(out)

