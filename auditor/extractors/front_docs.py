from __future__ import annotations

import re
from typing import List, Tuple, Optional

from ..models import FrontDoc, FrontDocsData, PiiRisk
from ..parsers.pdf_reader import extract_pages_text
from ..parsers.pii_scanner import scan_pages

_DOC_PATTERNS = {
    "申請書": ["都市更新事業計畫申請書", "都市更新申請書", "申  請  書"],
    "切結書": ["切結書"],
    "委託書": ["委  託  書", "委託書"],
    "審議資料表": ["臺北市都市更新審議資料表", "都市更新審議資料表"],
}

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
    """
    Extract ROC date from text.
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


def extract_front_docs(
    pdf_path: str, scan_pages_count: int = 15
) -> Tuple[FrontDocsData, List[PiiRisk]]:
    pages = extract_pages_text(pdf_path, 1, scan_pages_count)
    pii_risks = scan_pages(pages)

    docs: List[FrontDoc] = []
    # Map doc_type → (date, page_num) for date extraction
    date_by_source: dict[str, tuple[str, int]] = {}

    for page in pages:
        text = page["text"]
        page_num = page["page_num"]
        doc_type = _match_doc_type(text)
        if doc_type is None:
            continue

        purpose = _match_poa_purpose(text) if doc_type == "委託書" else None
        docs.append(FrontDoc(doc_type=doc_type, page=page_num, purpose=purpose))

        # Extract 報核日期 from 申請書/切結書/委託書 (not 審議資料表)
        if doc_type in _DATE_SOURCE_PRIORITY and doc_type not in date_by_source:
            date = _extract_roc_date(text)
            if date:
                date_by_source[doc_type] = (date, page_num)

    # Pick the highest-priority source
    report_date: Optional[str] = None
    report_date_page: Optional[int] = None
    report_date_source: Optional[str] = None
    for source in _DATE_SOURCE_PRIORITY:
        if source in date_by_source:
            report_date, report_date_page = date_by_source[source]
            report_date_source = source
            break

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
