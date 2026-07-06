"""
OCR reader for image-based PDF pages using PaddleOCR + pymupdf rendering.

Called as a third-pass fallback when both pdfplumber and pymupdf return empty
text, which happens with scanned/image-only PDF pages.

PaddleOCR model (~200–400 MB) is downloaded on first call and cached to
~/.paddleocr/ inside the container.  Subsequent calls reuse the loaded
model — the Reader is kept as a module-level singleton behind a threading.Lock.

OCR results are cached in a SQLite database at
~/.cache/urban-renewal-ocr/ocr_cache.db, keyed by (sha256 of first 64KB,
page_index, zoom, preprocess_version).  A cache hit skips PaddleOCR entirely.

Speed: ocr_pages() uses a producer-consumer pipeline — a background thread
renders PDF pages to numpy arrays while the main thread OCRs each page as it
arrives, overlapping rendering I/O with inference. (PaddleOCR must be called
per full-page image with detection enabled; list input forces det=False.)

Quality: raw pixels are fed to PaddleOCR at zoom=3.0. Aggressive preprocessing
(binarization/deskew) was measured to HURT digit recognition on dense scanned
tables (3→8 misreads), so it is disabled — see _preprocess_for_ocr(). Only
detections with confidence >= _OCR_MIN_CONFIDENCE are kept.
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
# entries are never returned for a different algorithm.
_PREPROCESS_VERSION = 3  # bumped: raw pixels + zoom 3.0 (was preproc + zoom 2.0)

# Confidence threshold — PaddleOCR detections below this score are dropped.
_OCR_MIN_CONFIDENCE = 0.5

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

_paddle_reader = None  # lazy singleton
_OCR_AVAILABLE: Optional[bool] = None  # None = not yet probed
_INIT_LOCK = threading.Lock()   # guards singleton initialisation
_INFER_LOCK = threading.Lock()  # PaddleOCR inference is not thread-safe


def _deskew(gray: "np.ndarray") -> "np.ndarray":  # type: ignore[name-defined]
    """Detect and correct page skew using Hough line analysis.

    Finds near-horizontal line segments, computes the median angle, and
    rotates the image to compensate.  Skips correction when:
    - No dominant angle is found (no lines / all vertical).
    - The detected angle exceeds ±15° (likely a false positive from
      decorative elements or non-text regions).

    Falls back to the original image if OpenCV is unavailable or any
    processing step raises.
    """
    try:
        import cv2
        import numpy as np

        edges = cv2.Canny(gray, 50, 150, apertureSize=3)
        lines = cv2.HoughLinesP(
            edges, 1, np.pi / 180,
            threshold=80, minLineLength=50, maxLineGap=10,
        )
        if lines is None:
            return gray

        angles = []
        for line in lines:
            x1, y1, x2, y2 = line[0]
            if x2 != x1:
                angle = np.degrees(np.arctan2(y2 - y1, x2 - x1))
                if abs(angle) < 45:  # near-horizontal lines only
                    angles.append(angle)

        if not angles:
            return gray

        skew = float(np.median(angles))
        if abs(skew) > 15:  # large angle is probably a false positive
            return gray

        h, w = gray.shape[:2]
        M = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), skew, 1.0)
        return cv2.warpAffine(
            gray, M, (w, h),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE,
        )
    except Exception:
        return gray


def _preprocess_for_ocr(img_array: "np.ndarray") -> "np.ndarray":  # type: ignore[name-defined]
    """Return the image unchanged — feed raw pixels to PaddleOCR.

    Measured on a real dense 審議資料表 (合家歡): aggressive preprocessing
    (deskew → CLAHE → denoise → Sauvola binarize) HURT digit recognition badly
    — binarization merges thin strokes so 3 reads as 8, poisoning dates/文號 and
    the regulation-year selection. Raw pixels at zoom=3.0 gave the best result
    (correct 「113」 ×8, zero 「118」 misreads) vs the worst with preprocessing
    (「113」 ×1, 「118」 ×6). PaddleOCR's own detector handles contrast/skew, so
    we pass raw pixels. The _deskew/_binarize helpers are kept for opt-in use.
    """
    return img_array


def _binarize(gray: "np.ndarray") -> "np.ndarray":  # type: ignore[name-defined]
    """Binarize a grayscale image using Sauvola thresholding.

    Sauvola accounts for local statistics (mean + std dev) which handles
    uneven illumination better than global or simple adaptive thresholding.
    Falls back to OpenCV adaptive Gaussian threshold if scikit-image is
    not installed.
    """
    try:
        import numpy as np
        from skimage.filters import threshold_sauvola

        thresh = threshold_sauvola(gray, window_size=25)
        return (gray >= thresh).astype(np.uint8) * 255
    except Exception:
        import cv2
        return cv2.adaptiveThreshold(
            gray, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            blockSize=31, C=10,
        )


def _parse_paddle_result(result: object) -> str:
    """Extract text from a PaddleOCR result for a single image.

    Filters detections whose confidence is below ``_OCR_MIN_CONFIDENCE``.

    PaddleOCR result format per image:
        [[box, (text, confidence)], ...]
    where box = [[x1,y1],[x2,y2],[x3,y3],[x4,y4]].
    """
    if not result:
        return ""
    lines = []
    for detection in result:
        if detection is None:
            continue
        try:
            text, confidence = detection[1]
            if confidence >= _OCR_MIN_CONFIDENCE:
                lines.append(str(text))
        except (IndexError, TypeError, ValueError):
            continue
    return "\n".join(lines)


def _get_reader():
    """Return module-level PaddleOCR Reader, initializing on first call."""
    global _paddle_reader, _OCR_AVAILABLE
    if _OCR_AVAILABLE is False:
        return None
    if _paddle_reader is not None:
        return _paddle_reader
    with _INIT_LOCK:
        if _paddle_reader is not None:  # double-checked locking
            return _paddle_reader
        try:
            from paddleocr import PaddleOCR
            _paddle_reader = PaddleOCR(
                use_angle_cls=True,
                lang="chinese_cht",
                use_gpu=False,
                show_log=False,
            )
            _OCR_AVAILABLE = True
            logger.info("PaddleOCR reader initialized (chinese_cht)")
            return _paddle_reader
        except Exception as exc:
            _OCR_AVAILABLE = False
            logger.warning("PaddleOCR not available: %s", exc)
            return None


def ocr_available() -> bool:
    """Return True if PaddleOCR and pymupdf are both importable."""
    return _FITZ_OK and (_OCR_AVAILABLE is not False)


def ocr_page(pdf_path: str, page_index: int, zoom: float = 3.0) -> str:
    """Run OCR on a single PDF page and return extracted text.

    Checks the SQLite cache first; on a miss, runs PaddleOCR and stores the
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

        with _INFER_LOCK:
            result = reader.ocr(img_array, cls=True)

        text = _parse_paddle_result(result[0] if result else None)

        if conn and fhash:
            _cache_put(conn, fhash, page_index, zoom, text)

        return text

    except Exception as exc:
        logger.warning("OCR failed for page index %d: %s", page_index, exc)
        return ""


def ocr_pages(pdf_path: str, page_indices: list[int], zoom: float = 3.0) -> dict[int, str]:
    """OCR multiple pages at once, reusing the same reader.

    Checks the SQLite cache for each page individually; only runs PaddleOCR for
    pages not already cached.  Cache failures are silently ignored.

    Uses a producer-consumer pipeline: a background thread renders PDF pages to
    numpy arrays while the main thread OCRs each page as it arrives, overlapping
    rendering I/O with inference. Each page is OCR'd with a separate detection
    pass (PaddleOCR rejects list input with detection enabled).

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

        # Producer: render pages in a background thread; bounded queue limits
        # peak memory while rendering and preprocessing overlap.
        render_q: queue.Queue = queue.Queue(maxsize=4)

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

        # Consume rendered pages as they arrive and OCR each one individually.
        # PaddleOCR's .ocr() must run per full-page image with detection enabled
        # (passing a list forces det=False, which treats inputs as pre-cropped
        # regions — wrong for whole pages). Per-page calls overlap with rendering.
        while True:
            item = render_q.get()
            if item is None:
                break
            idx, img_array = item
            if img_array is None:
                continue
            preprocessed = _preprocess_for_ocr(img_array)
            with _INFER_LOCK:
                result = reader.ocr(preprocessed, cls=True)
            text = _parse_paddle_result(result[0] if result else None)
            results[idx] = text
            if conn and fhash:
                _cache_put(conn, fhash, idx, zoom, text)
        renderer.join()

        return results

    except Exception as exc:
        logger.warning("OCR batch failed: %s", exc)
        return results
