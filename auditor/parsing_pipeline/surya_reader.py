"""Phase 2b: High-accuracy OCR for scanned / stamped pages via Surya.

Used as a complement to Docling for pages flagged is_scanned=True by the
triage phase (blurry scans, 用印切結書, old cadastral maps).

Reference: https://github.com/VikParuchuri/surya
"""
from __future__ import annotations

from typing import Dict, List


def ocr_pages(pdf_path: str, page_indices: List[int]) -> Dict[int, str]:
    """Run Surya OCR on the given 0-based *page_indices*.

    Returns a mapping of page_index → extracted text.
    Raises ImportError if surya is not installed.
    Raises NotImplementedError until this phase is implemented.
    """
    raise NotImplementedError("Phase 2b Surya OCR — to be implemented")
