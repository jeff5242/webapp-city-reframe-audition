"""Geometric reconstruction of the 審議資料表 grid from OCR bbox detections.

Sovereign on-prem path (副總 #3). PaddleOCR already returns a bbox + text per
detection, but the linear text pass discards geometry — flattening the dense
bordered grid destroys label→value adjacency, so plain regex over joined text is
unstable. Here we keep the bboxes and rebuild structure by geometry:

1. Two detections are on the same visual **row** when their y-centres are within
   a fraction of their text height.
2. For each **label** token (e.g. 「法定汽車停車位」), the value is the nearest
   detection to its right on the same row; if none, a trailing number inside the
   label token itself.

No extra model, no API key — works fully offline. Only *numeric* fields are
trusted here: numeric coercion drops a wrongly-picked text neighbour to None
(safe), whereas string fields (implementer/fill_date) could absorb garbage, so
those are left to the text-regex pass.

A *detection* is a dict ``{"text","conf","x0","x1","yc","h"}`` as produced by
``ocr_reader._detections_from_result``.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from .table_extractor import _FLOAT_FIELDS, _INT_FIELDS, _LABEL_FIELD_MAP, _coerce

_NUMERIC_FIELDS = _FLOAT_FIELDS | _INT_FIELDS

# Row-membership tolerance as a fraction of the taller box's height. 0.6 keeps
# same-line cells together while separating the next grid row.
_ROW_TOL_RATIO = 0.6

_TRAILING_NUM_RE = re.compile(r"([0-9][0-9,\.]*)\s*$")


def _same_row(a: dict, b: dict, tol_ratio: float = _ROW_TOL_RATIO) -> bool:
    """True when two detections sit on the same visual row (y-centres close)."""
    tol = max(a.get("h", 0.0), b.get("h", 0.0)) * tol_ratio
    return abs(a["yc"] - b["yc"]) <= tol


def _nearest_value_right(label: dict, dets: List[dict]) -> Optional[str]:
    """Text of the nearest detection to the right of *label* on the same row."""
    best: Optional[dict] = None
    best_gap: Optional[float] = None
    for d in dets:
        if d is label:
            continue
        if not _same_row(label, d):
            continue
        gap = d["x0"] - label["x1"]
        # Allow a small overlap (labels/values sometimes touch) but reject cells
        # that start clearly to the left of the label.
        if gap < -label.get("h", 0.0):
            continue
        if best_gap is None or gap < best_gap:
            best, best_gap = d, gap
    return best["text"] if best is not None else None


def reconstruct_fields(dets: List[dict]) -> Dict[str, object]:
    """Map OCR detections to numeric review-table fields by row geometry.

    Returns ``{field: coerced_value}`` for every numeric field whose label is
    found and whose value parses. Never raises on odd/empty input.
    """
    # Only detections carrying geometry can be placed on the grid.
    dets = [d for d in dets if "yc" in d and "x0" in d and "x1" in d]
    if not dets:
        return {}

    result: Dict[str, object] = {}
    for det in dets:
        text = (det.get("text") or "").strip()
        if not text:
            continue
        for keywords, field in _LABEL_FIELD_MAP:
            if field in result or field not in _NUMERIC_FIELDS:
                continue
            if any(kw in text for kw in keywords):
                raw = _nearest_value_right(det, dets)
                if raw is None:
                    m = _TRAILING_NUM_RE.search(text)
                    raw = m.group(1) if m else None
                value = _coerce(field, raw) if raw is not None else None
                if value is not None:
                    result[field] = value
                break
    return result
