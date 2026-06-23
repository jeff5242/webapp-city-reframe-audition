"""Phase 2b: High-accuracy OCR for scanned / stamped pages via Surya.

Used for pages flagged is_scanned=True by Phase 1 — blurry scans, 用印切結書,
and old cadastral maps where Docling's native extraction returns nothing.

Reference: https://github.com/VikParuchuri/surya
Install:   pip install surya-ocr
"""
from __future__ import annotations

from typing import Dict, List

_DEFAULT_DPI = 150
_SURYA_LANGS = ["zh", "en"]


def is_available() -> bool:
    """Return True if surya-ocr is installed and importable."""
    try:
        import surya  # noqa: F401
        return True
    except ImportError:
        return False


def ocr_pages(pdf_path: str, page_indices: List[int]) -> Dict[int, str]:
    """Run Surya OCR on the given 0-based *page_indices* in *pdf_path*.

    Returns {page_index: extracted_text}.  Pages that error are skipped.
    An empty dict is returned if surya-ocr is not installed.

    Each PDF page is rasterised at *_DEFAULT_DPI* DPI before OCR.

    Raises ImportError if surya-ocr or pymupdf is not installed.
    """
    if not page_indices:
        return {}

    try:
        from surya.ocr import run_ocr
        from surya.model.detection.model import (
            load_model as load_det_model,
            load_processor as load_det_processor,
        )
        from surya.model.recognition.model import load_model as load_rec_model
        from surya.model.recognition.processor import load_processor as load_rec_processor
    except ImportError:
        raise ImportError(
            "surya-ocr is required for Phase 2b OCR. Install: pip install surya-ocr"
        )

    try:
        import fitz  # type: ignore[import]
    except ImportError:
        raise ImportError("PyMuPDF (fitz) is required for PDF rasterisation. Install: pip install pymupdf")

    from PIL import Image

    # --- Rasterise requested pages ---
    images: List[Image.Image] = []
    valid_indices: List[int] = []
    doc = fitz.open(pdf_path)
    try:
        for idx in page_indices:
            if idx < 0 or idx >= len(doc):
                continue
            pix = doc[idx].get_pixmap(dpi=_DEFAULT_DPI)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            images.append(img)
            valid_indices.append(idx)
    finally:
        doc.close()

    if not images:
        return {}

    # --- Load Surya models (singleton-style — Surya caches internally) ---
    det_processor = load_det_processor()
    det_model = load_det_model()
    rec_model = load_rec_model()
    rec_processor = load_rec_processor()

    langs = [_SURYA_LANGS] * len(images)
    predictions = run_ocr(images, langs, det_model, det_processor, rec_model, rec_processor)

    result: Dict[int, str] = {}
    for page_idx, pred in zip(valid_indices, predictions):
        text = "\n".join(line.text for line in pred.text_lines if line.text.strip())
        result[page_idx] = text

    return result
