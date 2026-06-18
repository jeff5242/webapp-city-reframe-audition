from __future__ import annotations

import re
from typing import Optional

from ..models import ReviewTableData
from ..parsers.pdf_reader import extract_page_text, extract_pages_text, get_pdf_metadata

_TABLE_KEYWORDS = ["臺北市都市更新審議資料表", "都市更新審議資料表"]
# These markers only appear in the actual form, not in the TOC entry
_FORM_CONTENT_MARKERS = ["填表日期", "送審類別", "檔名", "實施方式"]
_SUBMISSION_TYPES = ["B-1", "A-1", "B-2", "C", "D"]
# Characters that indicate a checkbox is checked
_CHECKED = set("■▪●✓√☑◆")


def _parse_float(raw: str) -> Optional[float]:
    cleaned = re.sub(r'[,，\s平方公尺m²㎡]', '', raw)
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_int(raw: str) -> Optional[int]:
    digits = re.sub(r'[^\d]', '', raw)
    return int(digits) if digits else None


def _find_submission_type(text: str) -> Optional[str]:
    for t in _SUBMISSION_TYPES:
        idx = text.find(t)
        if idx == -1:
            continue
        nearby = text[max(0, idx - 6): idx + 3]
        if any(c in nearby for c in _CHECKED):
            return t
    return None


def _search(pattern: str, text: str, group: int = 1) -> Optional[str]:
    m = re.search(pattern, text)
    return m.group(group).strip() if m else None


def _find_review_table_page(pdf_path: str) -> Optional[int]:
    """Find the page containing the actual 審議資料表 form (not the TOC entry)."""
    meta = get_pdf_metadata(pdf_path)
    total = meta["total_pages"]
    pages = extract_pages_text(pdf_path, 1, min(30, total))

    for page in pages:
        text = page["text"]
        has_keyword = any(kw in text for kw in _TABLE_KEYWORDS)
        matched_markers = [m for m in _FORM_CONTENT_MARKERS if m in text]
        # Require 2+ form markers to avoid matching the TOC entry
        if has_keyword and len(matched_markers) >= 2:
            return page["page_num"]
    return None


def extract_review_table(pdf_path: str) -> Optional[ReviewTableData]:
    """Find and extract 審議資料表 from a PDF. Returns None if not found."""
    page_num = _find_review_table_page(pdf_path)
    if page_num is None:
        return None

    text = extract_page_text(pdf_path, page_num)
    return _parse_from_text(text, page_num)


def _parse_from_text(text: str, page_num: int) -> ReviewTableData:
    fill_date = _search(
        r'填表日期[：:\s]*(?:中華民國\s*)?(\d+\s*年\s*\d+\s*月\s*\d+\s*日)', text
    )

    implementer = _search(
        r'實施者[：:\s]*([^\n\r\t　]{4,40}(?:股份有限公司|有限公司|更新會|協會|開發))',
        text,
    )

    implementer_id = _search(r'統一編號[：:\s]*(\d{8})', text)

    submission_type = _find_submission_type(text)

    raw_name = _search(r'(?:計畫名稱|案\s*名)[：:\s]*([^\n\r]{10,120})', text)
    # Strip trailing checkbox noise like "（請勾選）□B-2:..."
    import re as _re
    case_name = _re.split(r'\s*[（(]請勾選[）)]|\s*□', raw_name)[0].strip() if raw_name else None

    # Bonus floor area: 合計獎勵樓地板面積 1,928.58m2
    bonus_floor_area: Optional[float] = None
    m = re.search(r'(?:申請額度|合計獎勵樓地板面積|獎勵面積合計|獎勵合計)[^\d\n]*(\d[\d,，.]+)', text)
    if m:
        bonus_floor_area = _parse_float(m.group(1))

    # Bonus limit: 都市更新容積獎勵上限 1,877.63m2
    bonus_limit: Optional[float] = None
    m = re.search(r'(?:獎勵上限|容積上限|容積獎勵上限)[^\d\n]*(\d[\d,，.]+)', text)
    if m:
        bonus_limit = _parse_float(m.group(1))

    # Base floor area: 基準容積 (the m² value, not 法定容積率 which is a %)
    base_floor_area: Optional[float] = None
    m = re.search(r'基準容積\s+(\d[\d,，.]+)\s*m', text)
    if m:
        base_floor_area = _parse_float(m.group(1))

    # Legal parking: may appear on the line AFTER the label (cross-line extraction)
    legal_parking: Optional[int] = None
    m = re.search(r'法定汽車停車位[^\n]*\n(\d+)\s*輛', text)
    if m:
        legal_parking = _parse_int(m.group(1))
    if legal_parking is None:
        m = re.search(r'法定[汽車]*停車位[：:\s]*(\d+)\s*輛', text)
        if m:
            legal_parking = _parse_int(m.group(1))

    # Accessible parking: (無障礙2輛) or 無障礙停車位 2輛
    accessible_parking: Optional[int] = None
    m = re.search(r'無障礙(\d+)輛', text)
    if m:
        accessible_parking = _parse_int(m.group(1))
    if accessible_parking is None:
        m = re.search(r'無障礙[停車位\s：:]*(\d+)\s*輛', text)
        if m:
            accessible_parking = _parse_int(m.group(1))

    # Actual parking: 實設汽車停車 (汽車only, skip 機車/裝卸)
    actual_parking: Optional[int] = None
    m = re.search(r'實設汽車停車[位\s]*(\d+)\s*輛', text)
    if m:
        actual_parking = _parse_int(m.group(1))

    # EV charging parking: (充電0輛) or 充電車位 0輛
    ev_parking: Optional[int] = None
    m = re.search(r'充電(\d+)輛', text)
    if m:
        ev_parking = _parse_int(m.group(1))
    if ev_parking is None:
        m = re.search(r'充電[停車位\s：:]*(\d+)\s*輛', text)
        if m:
            ev_parking = _parse_int(m.group(1))

    # Owner consent ratio
    owner_consent_ratio: Optional[float] = None
    m = re.search(r'土地所有權人[^%\d]*(\d+\.?\d*)\s*%', text)
    if m:
        try:
            owner_consent_ratio = float(m.group(1))
        except ValueError:
            pass

    return ReviewTableData(
        case_name=case_name,
        implementer=implementer,
        implementer_id=implementer_id,
        submission_type=submission_type,
        fill_date=fill_date,
        land_area=None,
        base_floor_area=base_floor_area,
        bonus_floor_area=bonus_floor_area,
        bonus_limit=bonus_limit,
        legal_parking=legal_parking,
        actual_parking=actual_parking,
        accessible_parking=accessible_parking,
        ev_parking=ev_parking,
        owner_consent_ratio=owner_consent_ratio,
        raw_page=page_num,
    )
