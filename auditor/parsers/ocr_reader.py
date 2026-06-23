"""
OCR reader for image-based PDF pages using EasyOCR + pymupdf rendering.

Called as a third-pass fallback when both pdfplumber and pymupdf return empty
text, which happens with scanned/image-only PDF pages.

EasyOCR model (~200 MB) is downloaded on first call and cached to
~/.EasyOCR/model/ inside the container.  Subsequent calls reuse the loaded
model — the Reader is kept as a module-level singleton.
"""
from __future__ import annotations

import io
import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import fitz as _fitz  # pymupdf — renders page to image
    _FITZ_OK = True
except ImportError:
    _FITZ_OK = False

_easyocr_reader = None  # lazy singleton
_OCR_AVAILABLE: Optional[bool] = None  # None = not yet probed


def _get_reader():
    """Return module-level EasyOCR Reader, initializing on first call."""
    global _easyocr_reader, _OCR_AVAILABLE
    if _OCR_AVAILABLE is False:
        return None
    if _easyocr_reader is not None:
        return _easyocr_reader
    try:
        import easyocr
        _easyocr_reader = easyocr.Reader(
            ["ch_tra", "en"],
            gpu=False,
            verbose=False,
        )
        _OCR_AVAILABLE = True
        logger.info("EasyOCR reader initialized (ch_tra + en)")
        return _easyocr_reader
    except Exception as exc:
        _OCR_AVAILABLE = False
        logger.warning("EasyOCR not available: %s", exc)
        return None


def ocr_available() -> bool:
    """Return True if EasyOCR and pymupdf are both importable."""
    return _FITZ_OK and (_OCR_AVAILABLE is not False)


def ocr_page(pdf_path: str, page_index: int, zoom: float = 2.0) -> str:
    """Run OCR on a single PDF page and return extracted text.

    Args:
        pdf_path:   absolute path to the PDF file
        page_index: 0-based page index
        zoom:       rendering scale factor (2.0 → ~150 dpi equivalent, good
                    enough for most scanned documents)

    Returns:
        Extracted text string, or empty string if OCR unavailable or failed.
    """
    if not _FITZ_OK:
        return ""
    reader = _get_reader()
    if reader is None:
        return ""

    try:
        import numpy as np
        from PIL import Image

        doc = _fitz.open(pdf_path)
        try:
            if page_index >= len(doc):
                return ""
            pix = doc[page_index].get_pixmap(matrix=_fitz.Matrix(zoom, zoom))
        finally:
            doc.close()

        img = Image.open(io.BytesIO(pix.tobytes("png")))
        img_array = np.array(img)

        results = reader.readtext(img_array, detail=0)
        return "\n".join(str(r) for r in results)

    except Exception as exc:
        logger.warning("OCR failed for page index %d: %s", page_index, exc)
        return ""


def ocr_pages(pdf_path: str, page_indices: list[int], zoom: float = 2.0) -> dict[int, str]:
    """OCR multiple pages at once, reusing the same reader.

    Returns a dict mapping 0-based page index → extracted text.
    """
    if not _FITZ_OK:
        return {}
    reader = _get_reader()
    if reader is None:
        return {}

    try:
        import numpy as np
        from PIL import Image

        doc = _fitz.open(pdf_path)
        results: dict[int, str] = {}
        try:
            for idx in page_indices:
                if idx >= len(doc):
                    continue
                pix = doc[idx].get_pixmap(matrix=_fitz.Matrix(zoom, zoom))
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                img_array = np.array(img)
                hits = reader.readtext(img_array, detail=0)
                results[idx] = "\n".join(str(r) for r in hits)
        finally:
            doc.close()
        return results

    except Exception as exc:
        logger.warning("OCR batch failed: %s", exc)
        return {}
