from __future__ import annotations

import re
from typing import Optional

from ..models import ReviewTableData
from ..parsers.pdf_reader import extract_page_text, extract_pages_text, get_pdf_metadata

_TABLE_KEYWORDS = ["臺北市都市更新審議資料表", "都市更新審議資料表"]
# These markers only appear in the actual form, not in the TOC entry
_FORM_CONTENT_MARKERS = ["填表日期", "送審類別", "檔名", "實施方式"]

# OCR-robust partial markers for scoring-based detection when OCR garbles the
# title / full field labels (e.g. 填表日期→填表耳期). Use prefixes that survive
# common OCR errors. The 審議資料表 hits many of these; other pages (e.g. the
# 獎勵容積計算頁) hit only a few.
_ROBUST_TABLE_MARKERS = [
    "填表", "送審", "辦理過程", "基準容積", "獎勵樓地板", "法定容積",
    "停車位", "更新單元", "實施者", "容積獎勵", "報核",
]
_ROBUST_SCORE_THRESHOLD = 4
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


# 辦理過程中「報核」列的日期：dot 格式 (112.09.08) 或 ROC 格式 (112年9月8日)
_PROCESS_DOT_DATE_RE = re.compile(r'(\d{3})\.(\d{1,2})\.(\d{1,2})')
_PROCESS_ROC_DATE_RE = re.compile(r'(\d{3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日')
# 報核文號編碼日期：字第 YYYMMDDnnnn 號 (如 11311140047 → 113/11/14)。
# OCR 對數字辨識較穩，文號是報核日最可靠的來源之一。
_PROCESS_DOCNUM_DATE_RE = re.compile(r'字\s*第\s*(\d{3})(\d{2})(\d{2})\d{2,}\s*號')

# 只計「報核」情境的日期，排除純浮水印「報核版」與公開展覽等其他階段
_NON_FILING_CONTEXT = ("公開展覽", "公聽會", "核定", "審議會", "幹事會")


def _valid_roc(y: int, mo: int, d: int) -> bool:
    return 100 <= y <= 130 and 1 <= mo <= 12 and 1 <= d <= 31


def _extract_filing_date_from_process(text: str) -> Optional[str]:
    """從審議資料表「辦理過程」抽取「報核」日期，回傳最新一筆。

    OCR 常把表格的標籤與數值拆到不同行，故採「就近」判讀：
    - 文號編碼日期（字第 YYYMMDD…號）：最可靠，一律採計。
    - 含「報核」的行 ± 相鄰行的 dot/ROC 日期：採計，但排除純「報核版」浮水印
      與公開展覽/核定/審議會等非報核階段。
    多筆取最新者（本次審議之報核日）。找不到回傳 None。
    """
    candidates: list[tuple[str, str]] = []  # (iso_for_sort, roc_display)

    def add(y: int, mo: int, d: int) -> None:
        if _valid_roc(y, mo, d):
            candidates.append((f"{y:03d}{mo:02d}{d:02d}", f"{y}年{mo}月{d}日"))

    lines = text.splitlines()
    for i, line in enumerate(lines):
        # ① 文號編碼日期 — 最可靠，任何行都採計
        for m in _PROCESS_DOCNUM_DATE_RE.finditer(line):
            add(int(m.group(1)), int(m.group(2)), int(m.group(3)))

        # ② 就近的 dot/ROC 日期：本行或上一行提到「報核」，且非其他階段
        context = (lines[i - 1] if i > 0 else "") + line
        if "報核" in context and not any(k in context for k in _NON_FILING_CONTEXT):
            # 純浮水印「報核版」（無其他報核字樣）不算
            if context.strip() in ("報核版", "報核"):
                continue
            for regex in (_PROCESS_DOT_DATE_RE, _PROCESS_ROC_DATE_RE):
                for m in regex.finditer(line):
                    add(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    if not candidates:
        return None
    return max(candidates, key=lambda c: c[0])[1]


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


def _find_review_table_page(pdf_path: str) -> tuple[Optional[int], str]:
    """Find the 審議資料表 page and return (page_num, text).

    First pass: pdfplumber (fast, works for text-based PDFs).
    Second pass: PaddleOCR on image pages (fallback for scanned PDFs).
    Returns (None, "") if not found.
    """
    import unicodedata

    meta = get_pdf_metadata(pdf_path)
    total = meta["total_pages"]
    scan_limit = min(30, total)
    pages = extract_pages_text(pdf_path, 1, scan_limit)

    # First pass — pdfplumber / pymupdf text
    image_page_indices: list[int] = []
    for page in pages:
        text = page["text"]
        has_keyword = any(kw in text for kw in _TABLE_KEYWORDS)
        matched_markers = [m for m in _FORM_CONTENT_MARKERS if m in text]
        if has_keyword and len(matched_markers) >= 2:
            return page["page_num"], text
        if page.get("_image_page"):
            image_page_indices.append(page["page_num"] - 1)

    if not image_page_indices:
        return None, ""

    # Second pass — OCR on scanned pages
    try:
        from ..parsers.ocr_reader import ocr_available, ocr_pages
        if not ocr_available():
            return None, ""
    except ImportError:
        return None, ""

    ocr_results = ocr_pages(pdf_path, image_page_indices)
    ocr_texts: dict[int, str] = {
        idx: unicodedata.normalize("NFKC", raw) for idx, raw in ocr_results.items()
    }

    # Pass A — title keyword + at least one marker (works when OCR is clean)
    for idx, text in ocr_texts.items():
        has_keyword = any(kw in text for kw in _TABLE_KEYWORDS)
        matched_markers = [m for m in _FORM_CONTENT_MARKERS if m in text]
        if has_keyword and len(matched_markers) >= 1:
            return idx + 1, text  # 1-based page num

    # Pass B — OCR often garbles the dense table's title and 「填表日期」
    # (e.g. 填表日期→填表耳期). Fall back to scoring pages by OCR-robust
    # characteristic fields and pick the best-scoring page above threshold.
    best_idx, best_score = None, 0
    for idx, text in ocr_texts.items():
        score = sum(1 for m in _ROBUST_TABLE_MARKERS if m in text)
        if score > best_score:
            best_idx, best_score = idx, score
    if best_idx is not None and best_score >= _ROBUST_SCORE_THRESHOLD:
        return best_idx + 1, ocr_texts[best_idx]

    return None, ""


def extract_review_table(pdf_path: str, enhance: bool = True) -> Optional[ReviewTableData]:
    """Find and extract 審議資料表 from a PDF. Returns None if not found.

    When *enhance* is True, gaps left by the text-regex pass are filled by the
    hybrid structured extractor (on-prem PP-Structure → Claude vision fallback),
    which is far more stable on this dense bordered grid.
    """
    page_num, text = _find_review_table_page(pdf_path)
    if page_num is None:
        return None

    # For text-based pages, text from _find_review_table_page already has content.
    # Re-extract only when the returned text is empty (shouldn't happen in practice).
    if not text.strip():
        text = extract_page_text(pdf_path, page_num)

    data = _parse_from_text(text, page_num)

    if enhance:
        try:
            from .table_extractor import enhance_review_table
            data = enhance_review_table(pdf_path, data)
        except Exception:  # pragma: no cover - defensive; enhancement is best-effort
            pass

    return data


def _parse_from_text(text: str, page_num: int) -> ReviewTableData:
    fill_date = _search(
        r'填表日期[：:\s]*(?:中華民國\s*)?(\d+\s*年\s*\d+\s*月\s*\d+\s*日)', text
    )

    # 報核日：辦理過程「報核」列的最新日期（優先於申請書作為版本選擇依據）
    report_filing_date = _extract_filing_date_from_process(text)

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
        report_filing_date=report_filing_date,
    )
