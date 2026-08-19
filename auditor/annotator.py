from __future__ import annotations

import re
from typing import List, Optional

from .models import Finding


def _page_from_evidence(evidence: str) -> Optional[int]:
    m = re.search(r'第\s*(\d+)\s*頁', evidence or "")
    return int(m.group(1)) if m else None


# 從 finding 文字抽出可在頁面上定位的數值字串（如 4,680.00）。
_VALUE_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d{2}")


def _values_to_locate(finding: Finding) -> List[str]:
    """取 applied_value（申報值）中的數字字串，供頁面全文搜尋定位。

    只用申報值，不用計算結果——計算值（如上限）通常不在原文上，
    搜不到就不畫，避免誤框。
    """
    return _VALUE_RE.findall(finding.applied_value or "")


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

        # 數值定位框：在頁面全文搜尋申報值（如 4,680.00），直接框住原文位置，
        # 讓審核者一眼看到問題數字在哪。搜不到就只留頁首警示帶，不誤框。
        for value in _values_to_locate(finding):
            for rect in pg.search_for(value)[:3]:
                box = pg.add_rect_annot(rect + (-2, -2, 2, 2))
                box.set_colors(stroke=stroke)
                box.set_border(width=1.6)
                box.set_info(
                    title=f"[{finding.rule_id}] {finding.rule_name}",
                    content=f"申報值 {value}：{finding.message}",
                )
                box.update()

    return doc.tobytes()
