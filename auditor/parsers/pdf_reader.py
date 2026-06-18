from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import List, Optional, Dict, Any

import pdfplumber


def _normalize(text: str) -> str:
    """Normalize CJK compatibility ideographs to canonical forms.

    PDFs often embed characters from U+F900-U+FAFF (CJK Compatibility Ideographs)
    that are visually identical to standard CJK characters but have different code
    points. NFKC normalization maps them to their canonical equivalents.
    """
    return unicodedata.normalize("NFKC", text)


def get_pdf_metadata(pdf_path: str) -> Dict[str, Any]:
    path = Path(pdf_path)
    with pdfplumber.open(pdf_path) as pdf:
        return {
            "path": str(path),
            "filename": path.name,
            "total_pages": len(pdf.pages),
            "file_size_mb": round(path.stat().st_size / (1024 * 1024), 1),
        }


def extract_page_text(pdf_path: str, page_num: int) -> str:
    """Extract text from a single page (1-indexed)."""
    with pdfplumber.open(pdf_path) as pdf:
        if page_num < 1 or page_num > len(pdf.pages):
            return ""
        return _normalize(pdf.pages[page_num - 1].extract_text() or "")


def extract_page_tables(pdf_path: str, page_num: int) -> List[List[List[Optional[str]]]]:
    """Extract tables from a single page (1-indexed)."""
    with pdfplumber.open(pdf_path) as pdf:
        if page_num < 1 or page_num > len(pdf.pages):
            return []
        return pdf.pages[page_num - 1].extract_tables() or []


def extract_pages_text(pdf_path: str, start: int, end: int) -> List[Dict[str, Any]]:
    """Extract text and tables from page range (1-indexed, inclusive)."""
    results = []
    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        clamped_end = min(end, total)
        for i in range(start - 1, clamped_end):
            page = pdf.pages[i]
            results.append({
                "page_num": i + 1,
                "text": _normalize(page.extract_text() or ""),
                "tables": page.extract_tables() or [],
            })
    return results


def find_page_with_keyword(pdf_path: str, keyword: str, max_pages: int = 30) -> Optional[int]:
    """Return 1-indexed page number of first page containing keyword."""
    normalized_keyword = _normalize(keyword)
    with pdfplumber.open(pdf_path) as pdf:
        limit = min(max_pages, len(pdf.pages))
        for i, page in enumerate(pdf.pages[:limit]):
            if normalized_keyword in _normalize(page.extract_text() or ""):
                return i + 1
    return None
