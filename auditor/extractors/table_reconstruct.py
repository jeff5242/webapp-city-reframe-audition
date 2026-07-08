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

# 純數字片段（含千分位逗號/小數點/空白），用來判斷可否續接
_NUM_FRAG_RE = re.compile(r"^[0-9][0-9,，.\s]*$")
# 懸空的千分位前綴，如「1,」「12,」——OCR 常把「1,406.00」切成「1,」+「406.00」
_DANGLING_THOUSANDS_RE = re.compile(r"[0-9][,，]\s*$")


def _same_row(a: dict, b: dict, tol_ratio: float = _ROW_TOL_RATIO) -> bool:
    """True when two detections sit on the same visual row (y-centres close)."""
    tol = max(a.get("h", 0.0), b.get("h", 0.0)) * tol_ratio
    return abs(a["yc"] - b["yc"]) <= tol


def _same_row_right(label: dict, dets: List[dict]) -> List[dict]:
    """同列、位於 label 右側的偵測，依 x0 由近到遠排序。"""
    out: List[dict] = []
    for d in dets:
        if d is label or not _same_row(label, d):
            continue
        gap = d["x0"] - label["x1"]
        # 容許少量重疊（標籤與值有時相黏），但排除明顯在標籤左邊的格子。
        if gap < -label.get("h", 0.0):
            continue
        out.append(d)
    out.sort(key=lambda d: d["x0"])
    return out


def _nearest_value_right(label: dict, dets: List[dict]) -> Optional[str]:
    """label 右側最近偵測的文字；若最近值以千分位逗號結尾（OCR 把「1,406.00」
    切成「1,」+「406.00」），續接後面的數字片段還原完整數字。

    續接條件嚴格：僅在目前累積值「以逗號結尾」時才接下一個純數字片段，因此不會
    把相鄰的獨立數字格誤併在一起（完整數字不會以逗號結尾）。
    """
    right = _same_row_right(label, dets)
    if not right:
        return None

    value = (right[0].get("text") or "").strip()
    # 最近值非數字 → 原樣回傳（數值欄位會由 _coerce 判為 None，安全）
    if not _NUM_FRAG_RE.match(value):
        return value

    for d in right[1:]:
        if not _DANGLING_THOUSANDS_RE.search(value):
            break  # 目前累積值已是完整數字，不再併入相鄰格
        frag = (d.get("text") or "").strip()
        if not _NUM_FRAG_RE.match(frag):
            break
        value += frag
    return value


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
