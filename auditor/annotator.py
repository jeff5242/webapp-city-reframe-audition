from __future__ import annotations

import re
from typing import List, Optional

from .models import Finding


def _page_from_evidence(evidence: str) -> Optional[int]:
    m = re.search(r'第\s*(\d+)\s*頁', evidence or "")
    return int(m.group(1)) if m else None


def annotate_pdf(pdf_path: str, findings: List[Finding]) -> bytes:
    """Add visible annotations to PDF pages that have failing/warning findings."""
    try:
        import fitz  # pymupdf
    except ImportError:
        with open(pdf_path, "rb") as f:
            return f.read()

    doc = fitz.open(pdf_path)
    total = len(doc)
    page_counts: dict[int, int] = {}

    for finding in findings:
        if finding.status not in ("fail", "warn"):
            continue
        page_num = _page_from_evidence(finding.evidence or "")
        if not page_num or page_num > total:
            continue

        count = page_counts.get(page_num, 0)
        page_counts[page_num] = count + 1

        pg = doc[page_num - 1]
        is_fail = finding.status == "fail"
        stroke = (0.85, 0.1, 0.1) if is_fail else (0.9, 0.5, 0.0)
        fill   = (1.0, 0.92, 0.92) if is_fail else (1.0, 0.97, 0.85)

        y0 = 6 + count * 30
        y1 = y0 + 26

        annot = pg.add_rect_annot(fitz.Rect(6, y0, pg.rect.width - 6, y1))
        annot.set_colors(stroke=stroke, fill=fill)
        annot.set_border(width=2)
        annot.set_info(
            title=f"[{finding.rule_id}] {finding.rule_name}",
            content=finding.message,
        )
        annot.update(opacity=0.85)

    return doc.tobytes()
