from __future__ import annotations

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


def extract_front_docs(
    pdf_path: str, scan_pages_count: int = 15
) -> Tuple[FrontDocsData, List[PiiRisk]]:
    pages = extract_pages_text(pdf_path, 1, scan_pages_count)
    pii_risks = scan_pages(pages)

    docs: List[FrontDoc] = []
    for page in pages:
        text = page["text"]
        page_num = page["page_num"]
        doc_type = _match_doc_type(text)
        if doc_type is None:
            continue

        purpose = _match_poa_purpose(text) if doc_type == "委託書" else None
        docs.append(FrontDoc(doc_type=doc_type, page=page_num, purpose=purpose))

    poa_docs = [d for d in docs if d.doc_type == "委託書"]

    front_docs = FrontDocsData(
        docs=tuple(docs),
        poa_count=len(poa_docs),
        has_application=any(d.doc_type == "申請書" for d in docs),
        has_affidavit=any(d.doc_type == "切結書" for d in docs),
        has_review_table=any(d.doc_type == "審議資料表" for d in docs),
    )

    return front_docs, pii_risks
