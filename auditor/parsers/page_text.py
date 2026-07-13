"""Per-page text provider: routes each page to the best recognition engine.

A single document interleaves native-text pages and scanned/image pages
(e.g. the 審議資料表 is a scan, later chapters are text). Reading only the text
layer silently drops every scanned page. This provider triages each page and
picks the right tool:

    text layer present  → pdfplumber / pymupdf (fast, exact)
    scanned / empty     → on-prem VLM transcription (VLM_ENDPOINT) → PaddleOCR

so every downstream extractor (front_docs, term_checker, PII, …) gets
consistent text regardless of page type. Sovereign: the scanned-page fallback
prefers the on-prem VLM; nothing leaves the machine when it is used.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .pdf_reader import _FITZ_AVAILABLE, _fitz_page_text, extract_page_text

# 原生文字層字元數門檻:低於此視為掃描/空白頁,改用 VLM/OCR。
_MIN_NATIVE_CHARS = 20

Source = Literal["text", "vlm", "ocr", "empty"]


@dataclass(frozen=True)
class PageRead:
    """一頁的辨識結果 + 用了哪個引擎(供觀測 / 除錯)。"""
    page: int          # 1-indexed
    text: str
    source: Source


def _native(pdf_path: str, page_num: int) -> str:
    """Native text layer via pdfplumber, then pymupdf as a second pass."""
    text = extract_page_text(pdf_path, page_num)
    if not text.strip() and _FITZ_AVAILABLE:
        text = _fitz_page_text(pdf_path, page_num - 1)
    return text


def read_page(
    pdf_path: str, page_num: int, min_native_chars: int = _MIN_NATIVE_CHARS
) -> PageRead:
    """Return the best text for a 1-indexed page, routing by page type.

    Text layer when it carries enough characters; otherwise on-prem VLM
    transcription (preferred — better on dense Traditional Chinese and
    sovereign), then PaddleOCR. Every fallback degrades gracefully.
    """
    native = _native(pdf_path, page_num)
    if len(native.strip()) >= min_native_chars:
        return PageRead(page_num, native, "text")

    # 掃描 / 近空白頁 → VLM 優先(主權 + 繁中較準),再退 PaddleOCR
    try:
        from .vlm_reader import transcribe_page, vlm_enabled
        if vlm_enabled():
            vlm_text = transcribe_page(pdf_path, page_num)
            if vlm_text and vlm_text.strip():
                return PageRead(page_num, vlm_text, "vlm")
    except Exception:  # pragma: no cover - defensive; VLM is best-effort
        pass

    try:
        from .ocr_reader import ocr_available, ocr_page
        if ocr_available():
            ocr_text = ocr_page(pdf_path, page_num - 1)
            if ocr_text and ocr_text.strip():
                return PageRead(page_num, ocr_text, "ocr")
    except Exception:  # pragma: no cover - defensive; OCR is best-effort
        pass

    # 沒有更好的來源,回原生(可能為空)
    return PageRead(page_num, native, "text" if native.strip() else "empty")


def page_text(pdf_path: str, page_num: int) -> str:
    """Convenience wrapper — just the routed text for a 1-indexed page."""
    return read_page(pdf_path, page_num).text


def pages_text(pdf_path: str, start: int, end: int) -> list:
    """Drop-in for ``extract_pages_text`` that fills scanned pages via the VLM.

    Same output shape (``page_num``/``text``/``tables``/``_image_page``) plus a
    ``source`` key. A scanned page (no text layer) is transcribed by the on-prem
    VLM **only when VLM_ENDPOINT is set** — deliberately NOT falling back to the
    slow CPU OCR in bulk, so a text/scanned-interleaved document stays fast and a
    CPU-only host never regresses to the /audit timeout. Text pages are untouched.
    """
    from .pdf_reader import extract_pages_text
    from .vlm_reader import transcribe_page, vlm_enabled

    base = extract_pages_text(pdf_path, start, end, ocr_image_pages=False)
    use_vlm = vlm_enabled()
    out = []
    for entry in base:
        if entry["_image_page"] and use_vlm:
            t = transcribe_page(pdf_path, entry["page_num"])
            if t and t.strip():
                out.append({**entry, "text": t, "_image_page": False, "source": "vlm"})
                continue
        out.append({**entry, "source": "text" if entry["text"].strip() else "scanned"})
    return out
