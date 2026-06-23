"""Phase 1: Triage — classify each PDF page as text-based or scanned image.

Uses PyMuPDF for fast per-page analysis before routing to heavy OCR/layout
engines. Image pages below the character-density threshold are flagged for
Docling/Surya processing in phase 2.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

_TEXT_CHAR_THRESHOLD = 0.05  # fraction of page area covered by characters


@dataclass(frozen=True)
class PageClass:
    page_num: int
    is_scanned: bool
    char_count: int


def triage_pdf(pdf_path: str) -> List[PageClass]:
    """Classify every page in *pdf_path* as text or scanned.

    Returns a list of PageClass objects (one per page).
    Raises RuntimeError if PyMuPDF is unavailable.
    """
    raise NotImplementedError("Phase 1 triage — to be implemented")
