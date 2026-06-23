"""Phase 1: Triage — classify each PDF page as text-based or scanned image.

Uses PyMuPDF for fast per-page analysis. Pages with fewer than _MIN_TEXT_CHARS
extractable characters are flagged is_scanned=True and routed to Docling/Surya
in Phase 2. Text-rich pages are extracted directly (fast path).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

_MIN_TEXT_CHARS = 30           # pages below this char count are treated as scanned
_IMAGE_COVERAGE_THRESHOLD = 0.30  # image area / page area — secondary signal


@dataclass(frozen=True)
class PageClass:
    page_num: int       # 1-indexed
    is_scanned: bool
    char_count: int
    text_fraction: float    # text-block area / page area
    image_fraction: float   # image area / page area


def triage_pdf(pdf_path: str) -> List[PageClass]:
    """Classify every page in *pdf_path* as text or scanned.

    Opens the PDF with PyMuPDF, inspects text blocks and embedded images per
    page, and returns one PageClass per page.

    Raises RuntimeError if PyMuPDF (fitz) is unavailable.
    """
    try:
        import fitz  # type: ignore[import]
    except ImportError:
        raise RuntimeError("PyMuPDF (fitz) required for triage. Install: pip install pymupdf")

    results: List[PageClass] = []
    doc = fitz.open(pdf_path)
    try:
        for i, page in enumerate(doc):
            rect = page.rect
            page_area = rect.width * rect.height if (rect.width > 0 and rect.height > 0) else 1.0

            # --- Text analysis ---
            blocks = page.get_text("blocks")  # (x0,y0,x1,y1,text,block_no,type)
            text_blocks = [b for b in blocks if b[6] == 0]  # type 0 = text
            text_area = sum((b[2] - b[0]) * (b[3] - b[1]) for b in text_blocks)
            char_count = sum(len(b[4].strip()) for b in text_blocks)
            text_fraction = text_area / page_area

            # --- Image analysis ---
            images = page.get_images(full=True)
            image_area = 0.0
            for img in images:
                xref = img[0]
                try:
                    for r in page.get_image_rects(xref):
                        image_area += r.get_area()
                except Exception:
                    pass
            image_fraction = image_area / page_area

            # A page is scanned when it has too few extractable characters.
            # Image fraction is a secondary confirmation signal.
            is_scanned = char_count < _MIN_TEXT_CHARS

            results.append(PageClass(
                page_num=i + 1,
                is_scanned=is_scanned,
                char_count=char_count,
                text_fraction=round(text_fraction, 4),
                image_fraction=round(image_fraction, 4),
            ))
    finally:
        doc.close()

    return results


def scanned_page_indices(classes: List[PageClass]) -> List[int]:
    """Return 0-based indices of pages flagged as scanned."""
    return [c.page_num - 1 for c in classes if c.is_scanned]


def text_page_indices(classes: List[PageClass]) -> List[int]:
    """Return 0-based indices of pages with extractable text."""
    return [c.page_num - 1 for c in classes if not c.is_scanned]
