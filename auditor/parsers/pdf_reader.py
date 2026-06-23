from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import List, Optional, Dict, Any

import pdfplumber
try:
    import fitz as _fitz  # pymupdf – optional second-pass extractor
    _FITZ_AVAILABLE = True
except ImportError:
    _FITZ_AVAILABLE = False


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


def _fitz_page_text(pdf_path: str, page_index: int) -> str:
    """Fallback: extract text from a single page via pymupdf (0-indexed)."""
    if not _FITZ_AVAILABLE:
        return ""
    try:
        doc = _fitz.open(pdf_path)
        text = doc[page_index].get_text() if page_index < len(doc) else ""
        doc.close()
        return _normalize(text or "")
    except Exception:
        return ""


def extract_pages_text(
    pdf_path: str,
    start: int,
    end: int,
    ocr_image_pages: bool = False,
) -> List[Dict[str, Any]]:
    """Extract text and tables from page range (1-indexed, inclusive).

    Pass order per page:
      1. pdfplumber  — handles native-text PDFs
      2. pymupdf     — second-pass for hybrid PDFs
      3. EasyOCR     — third-pass for scanned/image-only pages
                       (only when ocr_image_pages=True, slower)
    """
    from .ocr_reader import ocr_pages

    results = []
    image_page_indices: list[int] = []  # 0-based indices needing OCR

    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        clamped_end = min(end, total)
        for i in range(start - 1, clamped_end):
            page = pdf.pages[i]
            text = _normalize(page.extract_text() or "")
            tables = page.extract_tables() or []
            if not text.strip() and _FITZ_AVAILABLE:
                text = _fitz_page_text(pdf_path, i)
            results.append({
                "page_num": i + 1,
                "text": text,
                "tables": tables,
                "_image_page": not bool(text.strip()),
            })
            if not text.strip() and ocr_image_pages:
                image_page_indices.append(i)

    # Third pass: batch OCR image pages (expensive — only when requested)
    if ocr_image_pages and image_page_indices:
        ocr_results = ocr_pages(pdf_path, image_page_indices)
        for entry in results:
            idx = entry["page_num"] - 1
            if idx in ocr_results and ocr_results[idx].strip():
                entry["text"] = _normalize(ocr_results[idx])
                entry["_image_page"] = False  # OCR recovered text

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
