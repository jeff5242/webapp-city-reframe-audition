"""PII redaction: apply permanent black boxes over detected PII in PDFs.

Toggle via environment variable:
    AUTO_MASK_PII=true   (default) — mask HIGH-severity PII in the download PDF
    AUTO_MASK_PII=false            — disable masking (e.g., internal review only)

Only HIGH-severity items (residential_address, id_number) are masked by default.
MEDIUM items (phone numbers) are left to human judgement.

For text-based PDF pages: uses PyMuPDF redaction API (removes embedded text AND
draws a filled black rectangle — content is permanently gone from the output bytes).

For scanned/image pages: the PII text came from OCR and is not embedded in the PDF,
so coordinate-based redaction is not possible. A visible warning label is placed on
those pages so a human reviewer knows manual masking is required before distribution.
"""
from __future__ import annotations

import logging
import os
from typing import List

from .models import PiiRisk

log = logging.getLogger(__name__)


def masking_enabled() -> bool:
    """Return True if AUTO_MASK_PII env var is set to a truthy value (default: true)."""
    return os.getenv("AUTO_MASK_PII", "true").lower() not in ("0", "false", "no", "off")


def mask_pii(pdf_path: str, pii_risks: List[PiiRisk]) -> bytes:
    """Return PDF bytes with HIGH-severity PII regions permanently blacked out.

    If masking is disabled or PyMuPDF is unavailable, returns the original bytes.
    """
    try:
        import fitz
    except ImportError:
        with open(pdf_path, "rb") as f:
            return f.read()

    high_risks = [r for r in pii_risks if r.severity == "HIGH"]
    if not high_risks:
        with open(pdf_path, "rb") as f:
            return f.read()

    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    text_masked = 0
    scan_warned: set[int] = set()

    for risk in high_risks:
        page_idx = risk.page - 1
        if page_idx < 0 or page_idx >= total_pages:
            continue

        pg = doc[page_idx]
        rects = pg.search_for(risk.value)

        if rects:
            for rect in rects:
                pg.add_redact_annot(rect, fill=(0, 0, 0))
            text_masked += 1
            log.info("PII masked: '%s…' on page %d", risk.value[:15], risk.page)
        elif risk.page not in scan_warned:
            # Scanned page — place a prominent warning banner at the top
            scan_warned.add(risk.page)
            _add_scan_warning(pg)
            log.warning(
                "PII on scanned page %d could not be auto-masked — manual review required",
                risk.page,
            )

    if text_masked > 0:
        doc.apply_redactions()

    result = doc.tobytes()
    doc.close()
    return result


def _add_scan_warning(pg) -> None:
    """Draw an orange warning banner at the top of a scanned page."""
    try:
        import fitz
        banner = fitz.Rect(0, 0, pg.rect.width, 28)
        pg.draw_rect(banner, color=(1, 0.5, 0), fill=(1, 0.85, 0.5), width=0)
        pg.insert_text(
            fitz.Point(6, 18),
            "⚠ 此頁含個資（掃描頁），系統無法自動遮蓋，請手動塗黑後再發送",
            fontsize=9,
            color=(0.5, 0.2, 0),
        )
    except Exception:
        pass
