"""
OCR reader for image-based PDF pages using EasyOCR + pymupdf rendering.

Called as a third-pass fallback when both pdfplumber and pymupdf return empty
text, which happens with scanned/image-only PDF pages.

EasyOCR model (~200 MB) is downloaded on first call and cached to
~/.EasyOCR/model/ inside the container.  Subsequent calls reuse the loaded
model — the Reader is kept as a module-level singleton.

OCR results are also cached in a SQLite database at
~/.cache/urban-renewal-ocr/ocr_cache.db, keyed by (sha256 of first 64KB,
page_index, zoom).  A cache hit skips EasyOCR entirely.

Speed: ocr_pages() uses a producer-consumer pipeline — a background thread
renders PDF pages to numpy arrays while the main thread runs OCR on the
previous page, overlapping I/O with inference.

Quality: _preprocess_for_ocr() applies CLAHE contrast enhancement + fast
denoising + adaptive binarization before passing to EasyOCR, which
significantly reduces garbled-character errors on scanned government documents.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import logging
import queue
import sqlite3
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OCR result cache
# ---------------------------------------------------------------------------
_CACHE_DIR = Path.home() / ".cache" / "urban-renewal-ocr"
_CACHE_DB = _CACHE_DIR / "ocr_cache.db"
_HASH_BYTES = 64 * 1024  # first 64 KB for fast, stable identification

# Bump this constant whenever the preprocessing pipeline changes so old cache
# entries are never served for a different algorithm.
_PREPROCESS_VERSION = 2

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ocr_cache (
    file_hash          TEXT    NOT NULL,
    page_idx           INTEGER NOT NULL,
    zoom               REAL    NOT NULL,
    preprocess_version INTEGER NOT NULL,
    text               TEXT    NOT NULL,
    PRIMARY KEY (file_hash, page_idx, zoom, preprocess_version)
)
"""


def _get_cache_conn() -> Optional[sqlite3.Connection]:
    """Return a SQLite connection, creating the DB and table if needed.

    Migrates the schema automatically: if the existing table is missing the
    ``preprocess_version`` column (old schema), it is dropped so the new
    schema can be created.  Cache data is a pure performance optimisation, so
    dropping old rows on schema change is acceptable.

    Returns None if anything goes wrong so callers can skip caching silently.
    """
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(_CACHE_DB), check_same_thread=False)
        existing_cols = {
            row[1]
            for row in conn.execute("PRAGMA table_info(ocr_cache)")
        }
        if existing_cols and "preprocess_version" not in existing_cols:
            conn.execute("DROP TABLE ocr_cache")
            conn.commit()
            logger.debug("OCR cache schema migrated (dropped old table)")
        conn.execute(_CREATE_TABLE_SQL)
        conn.commit()
        return conn
    except Exception as exc:  # pragma: no cover
        logger.debug("OCR cache init failed (non-fatal): %s", exc)
        return None


def _file_hash(pdf_path: str) -> Optional[str]:
    """Return sha256 hex of the first 64 KB of *pdf_path*, or None on error."""
    with contextlib.suppress(Exception):
        with open(pdf_path, "rb") as fh:
            return hashlib.sha256(fh.read(_HASH_BYTES)).hexdigest()
    return None


def _cache_get(conn: sqlite3.Connection, fhash: str, page_idx: int, zoom: float) -> Optional[str]:
    """Return cached OCR text, or None if not found."""
    with contextlib.suppress(Exception):
        row = conn.execute(
            "SELECT text FROM ocr_cache"
            " WHERE file_hash=? AND page_idx=? AND zoom=? AND preprocess_version=?",
            (fhash, page_idx, zoom, _PREPROCESS_VERSION),
        ).fetchone()
        return row[0] if row else None
    return None


def _cache_put(conn: sqlite3.Connection, fhash: str, page_idx: int, zoom: float, text: str) -> None:
    """Write an OCR result to the cache (ignore failures)."""
    with contextlib.suppress(Exception):
        conn.execute(
            "INSERT OR REPLACE INTO ocr_cache"
            " (file_hash, page_idx, zoom, preprocess_version, text) VALUES (?,?,?,?,?)",
            (fhash, page_idx, zoom, _PREPROCESS_VERSION, text),
        )
        conn.commit()

try:
    import fitz as _fitz  # pymupdf — renders page to image
    _FITZ_OK = True
except ImportError:
    _FITZ_OK = False

_easyocr_reader = None  # lazy singleton
_OCR_AVAILABLE: Optional[bool] = None  # None = not yet probed


def _preprocess_for_ocr(img_array: "np.ndarray") -> "np.ndarray":  # type: ignore[name-defined]
    """Enhance image quality before OCR: CLAHE → denoise → adaptive binarize.

    Fixes common scanned-document issues: uneven lighting, scanner noise,
    and low contrast that cause EasyOCR to produce garbled Traditional Chinese.
    Falls back to original array if OpenCV is unavailable.
    """
    try:
        import cv2
        import numpy as np

        if img_array.ndim == 3 and img_array.shape[2] == 4:
            gray = cv2.cvtColor(img_array, cv2.COLOR_RGBA2GRAY)
        elif img_array.ndim == 3:
            gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
        else:
            gray = img_array.copy()

        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)
        denoised = cv2.fastNlMeansDenoising(enhanced, h=10, templateWindowSize=7, searchWindowSize=21)
        binary = cv2.adaptiveThreshold(
            denoised, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            blockSize=31, C=10,
        )
        return cv2.cvtColor(binary, cv2.COLOR_GRAY2RGB)
    except Exception:
        return img_array


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

    Checks the SQLite cache first; on a miss, runs EasyOCR and stores the
    result.  Cache failures are silently ignored so OCR always proceeds.

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

    fhash = _file_hash(pdf_path)
    conn = _get_cache_conn() if fhash else None

    # Cache lookup
    if conn and fhash:
        cached = _cache_get(conn, fhash, page_index, zoom)
        if cached is not None:
            return cached

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
        img_array = _preprocess_for_ocr(np.array(img))

        results = reader.readtext(img_array, detail=0)
        text = "\n".join(str(r) for r in results)

        if conn and fhash:
            _cache_put(conn, fhash, page_index, zoom, text)

        return text

    except Exception as exc:
        logger.warning("OCR failed for page index %d: %s", page_index, exc)
        return ""


def ocr_pages(pdf_path: str, page_indices: list[int], zoom: float = 2.0) -> dict[int, str]:
    """OCR multiple pages at once, reusing the same reader.

    Checks the SQLite cache for each page individually; only runs EasyOCR for
    pages not already cached.  Cache failures are silently ignored.

    Returns a dict mapping 0-based page index → extracted text.
    """
    if not _FITZ_OK:
        return {}
    reader = _get_reader()
    if reader is None:
        return {}

    fhash = _file_hash(pdf_path)
    conn = _get_cache_conn() if fhash else None

    results: dict[int, str] = {}
    uncached_indices: list[int] = []

    # Populate from cache where possible
    if conn and fhash:
        for idx in page_indices:
            cached = _cache_get(conn, fhash, idx, zoom)
            if cached is not None:
                results[idx] = cached
            else:
                uncached_indices.append(idx)
    else:
        uncached_indices = list(page_indices)

    if not uncached_indices:
        return results

    try:
        import numpy as np
        from PIL import Image

        # Producer-consumer: background thread renders pages while main thread
        # runs OCR, overlapping PDF I/O with inference for ~20-30% speedup.
        render_q: queue.Queue = queue.Queue(maxsize=3)

        def _render_worker() -> None:
            doc = _fitz.open(pdf_path)
            try:
                for idx in uncached_indices:
                    if idx >= len(doc):
                        render_q.put((idx, None))
                        continue
                    pix = doc[idx].get_pixmap(matrix=_fitz.Matrix(zoom, zoom))
                    img = Image.open(io.BytesIO(pix.tobytes("png")))
                    render_q.put((idx, np.array(img)))
            except Exception as exc:
                logger.warning("Render worker error: %s", exc)
            finally:
                doc.close()
                render_q.put(None)  # sentinel

        renderer = threading.Thread(target=_render_worker, daemon=True)
        renderer.start()

        while True:
            item = render_q.get()
            if item is None:
                break
            idx, img_array = item
            if img_array is None:
                continue
            preprocessed = _preprocess_for_ocr(img_array)
            hits = reader.readtext(preprocessed, detail=0)
            text = "\n".join(str(r) for r in hits)
            results[idx] = text
            if conn and fhash:
                _cache_put(conn, fhash, idx, zoom, text)

        renderer.join()
        return results

    except Exception as exc:
        logger.warning("OCR batch failed: %s", exc)
        return results  # return whatever was already retrieved from cache
