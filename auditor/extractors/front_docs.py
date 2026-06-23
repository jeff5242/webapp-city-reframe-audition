from __future__ import annotations

import re
from typing import List, Tuple, Optional

from ..models import FrontDoc, FrontDocsData, PiiRisk
from ..parsers.pdf_reader import extract_pages_text
from ..parsers.pii_scanner import scan_pages

_DOC_PATTERNS = {
    "申請書": [
        "都市更新事業計畫及權利變換計畫申請書",  # combined plan variant
        "都市更新事業計畫申請書",
        "都市更新申請書",
        "申  請  書",
    ],
    "切結書": ["切結書"],
    "委託書": ["委  託  書", "委託書"],
    "審議資料表": ["臺北市都市更新審議資料表", "都市更新審議資料表"],
}

# Detect TOC/index pages to avoid mis-classifying them as content pages
_TOC_MARKERS = ["目 錄", "目錄", "............"]

_POA_PURPOSES = [
    ("都更規劃", ["都更規劃", "都市更新規劃", "都更整合"]),
    ("建築設計", ["建築設計", "建築師", "建築規劃設計"]),
    ("地政業務", ["地政業務", "地政士", "土地登記"]),
    ("估價業務", ["估價", "不動產估價師"]),
]

# Priority order for which document's date to use as 報核日期
_DATE_SOURCE_PRIORITY = ["申請書", "切結書", "委託書"]


def _match_doc_type(text: str) -> Optional[str]:
    for doc_type, patterns in _DOC_PATTERNS.items():
        if any(p in text for p in patterns):
            return doc_type
    return None


def _match_poa_purpose(text: str) -> Optional[str]:
    for purpose, keywords in _POA_PURPOSES:
        if any(kw in text for kw in keywords):
            return purpose
    return None


def _extract_roc_date(text: str) -> Optional[str]:
    """Extract ROC date from text.

    Handles spaced chars like '中 華 民 國 1 1 2 年 1 2 月 2 6 日'
    and compact forms like '中華民國112年12月26日'.
    Returns e.g. '112年12月26日'.
    """
    m = re.search(
        r'中\s*華\s*民\s*國\s*((?:\d\s*){2,4})年\s*((?:\d\s*){1,2})月\s*((?:\d\s*){1,2})日',
        text
    )
    if not m:
        return None
    try:
        year  = int(re.sub(r'\s+', '', m.group(1)))
        month = int(re.sub(r'\s+', '', m.group(2)))
        day   = int(re.sub(r'\s+', '', m.group(3)))
        if 100 <= year <= 130 and 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year}年{month}月{day}日"
    except ValueError:
        pass
    return None


# Pattern for compact ROC date without 中華民國 prefix (e.g. "依據114年5月28日謄本")
_COMPACT_DATE_RE = re.compile(r'(\d{3})年(\d{1,2})月(\d{1,2})日')

# Keywords that indicate the date is the filing/report date
_FILED_DATE_KEYWORDS = ["謄本", "報核", "送件", "申請日", "報核日"]


def _extract_filed_date_from_supplement(text: str) -> Optional[str]:
    """Fallback: infer 報核日期 from supplementary response text.

    When front-doc pages are image-based and unreadable, supplementary
    committee-response pages often mention the 謄本 date that was used
    at the time of filing, e.g. '依據114年5月28日謄本修正所有權人'.
    The 謄本 date is taken at filing and reliably proxies 報核日期.
    """
    for kw in _FILED_DATE_KEYWORDS:
        # Find keyword positions and look backward ≤30 chars for a date
        for match_kw in re.finditer(re.escape(kw), text):
            start = max(0, match_kw.start() - 30)
            snippet = text[start:match_kw.start() + len(kw) + 5]
            m = _COMPACT_DATE_RE.search(snippet)
            if m:
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if 100 <= y <= 130 and 1 <= mo <= 12 and 1 <= d <= 31:
                    return f"{y}年{mo}月{d}日"
    return None


_FRONT_DOC_MAX_PAGE = 20  # pages beyond this are treated as supplementary material

# ROC date regex for OCR output: accepts partial matches like "114年5月28日"
# that appear without the "中華民國" prefix (common in OCR of spread layouts)
_OCR_COMPACT_DATE_RE = re.compile(
    r'(?:中\s*華\s*民\s*國\s*)?'   # optional prefix
    r'(\d{3})\s*年\s*'
    r'(\d{1,2})\s*月\s*'
    r'(\d{1,2})\s*日'
)


def _extract_roc_date_ocr(text: str) -> Optional[str]:
    """More permissive date extraction for OCR text (prefix optional)."""
    m = _OCR_COMPACT_DATE_RE.search(text)
    if not m:
        return None
    try:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 100 <= y <= 130 and 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y}年{mo}月{d}日"
    except ValueError:
        pass
    return None


def extract_front_docs(
    pdf_path: str,
    scan_pages_count: int = 60,
    use_ocr: bool = True,
) -> Tuple[FrontDocsData, List[PiiRisk]]:
    # First pass: pdfplumber + pymupdf (no OCR)
    pages = extract_pages_text(pdf_path, 1, scan_pages_count, ocr_image_pages=False)

    # Identify which front-section pages are still empty after two passes
    image_page_indices = [
        p["page_num"] - 1
        for p in pages
        if p.get("_image_page") and p["page_num"] <= _FRONT_DOC_MAX_PAGE
    ]

    # Second pass: OCR on image pages (front section only)
    if use_ocr and image_page_indices:
        import unicodedata
        from ..parsers.ocr_reader import ocr_available, ocr_pages
        if ocr_available():
            ocr_results = ocr_pages(pdf_path, image_page_indices)
            for entry in pages:
                idx = entry["page_num"] - 1
                if idx in ocr_results and ocr_results[idx].strip():
                    entry["text"] = unicodedata.normalize("NFKC", ocr_results[idx])
                    entry["_image_page"] = False

    pii_risks = scan_pages(pages)

    docs: List[FrontDoc] = []
    date_by_source: dict[str, tuple[str, int]] = {}
    # Non-image text from supplementary pages for fallback date search
    supplement_texts: list[str] = []

    for page in pages:
        text = page["text"]
        page_num = page["page_num"]

        is_toc = any(marker in text for marker in _TOC_MARKERS)
        is_front_section = page_num <= _FRONT_DOC_MAX_PAGE

        if is_toc:
            # TOC page: check ALL doc types — a single TOC lists multiple headings.
            for dt, patterns in _DOC_PATTERNS.items():
                if any(p in text for p in patterns):
                    if not any(d.doc_type == dt for d in docs):
                        docs.append(FrontDoc(doc_type=dt, page=page_num, purpose=None))
        elif is_front_section:
            # Content page within front-matter range: match doc type and extract date.
            # Pages recovered by OCR use a more permissive date regex (no prefix required).
            doc_type = _match_doc_type(text)
            if doc_type is not None:
                purpose = _match_poa_purpose(text) if doc_type == "委託書" else None
                docs.append(FrontDoc(doc_type=doc_type, page=page_num, purpose=purpose))
                if doc_type in _DATE_SOURCE_PRIORITY and doc_type not in date_by_source:
                    was_ocr = not page.get("_image_page", True)
                    date = (
                        _extract_roc_date_ocr(text) if was_ocr
                        else _extract_roc_date(text)
                    )
                    if date:
                        date_by_source[doc_type] = (date, page_num)
        else:
            # Supplementary pages (>20): accumulate for fallback date search only
            if text.strip():
                supplement_texts.append(text)

    # Pick the highest-priority source
    report_date: Optional[str] = None
    report_date_page: Optional[int] = None
    report_date_source: Optional[str] = None
    for source in _DATE_SOURCE_PRIORITY:
        if source in date_by_source:
            report_date, report_date_page = date_by_source[source]
            report_date_source = source
            break

    # Fallback: when front-doc pages are image-based (no date extracted), look
    # in supplementary response text for 謄本/報核 date references.
    if report_date is None and supplement_texts:
        combined = "\n".join(supplement_texts)
        fallback_date = _extract_filed_date_from_supplement(combined)
        if fallback_date:
            report_date = fallback_date
            report_date_source = "補正回應（謄本日期）"

    poa_docs = [d for d in docs if d.doc_type == "委託書"]

    front_docs = FrontDocsData(
        docs=tuple(docs),
        poa_count=len(poa_docs),
        has_application=any(d.doc_type == "申請書" for d in docs),
        has_affidavit=any(d.doc_type == "切結書" for d in docs),
        has_review_table=any(d.doc_type == "審議資料表" for d in docs),
        report_date=report_date,
        report_date_page=report_date_page,
        report_date_source=report_date_source,
    )

    return front_docs, pii_risks
