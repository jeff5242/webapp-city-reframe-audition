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
``ocr_reader._detections_from_predict``.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from .table_extractor import (
    _FLOAT_FIELDS,
    _INT_FIELDS,
    _coerce,
    _iter_label_map,
    _label_matches,
)

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


# 實設汽車停車位常拆成 平面/機械 子列，無單一總數格 → 以 平面+機械 補出總數。
_PARKING_MAX = 5000  # 車位數合理上限，防止把面積等大數誤當車位

# 送審類別勾選框：實心勾選記號 + 類別碼/敘述。OCR 對 ■/□ 判讀不穩，故只在偵測到
# 實心記號緊鄰某類別時才回傳，否則維持 None（人工確認，不亂猜）。
_FILLED_MARKS = "■▇█☑☒✓✔▪◼◾❚▓"
_SUBMISSION_TYPE_KEYS = [
    ("A-1", ("A-1", "A－1", "送件版", "公開展覽")),
    ("B-1", ("B-1", "B－1", "168專案小組", "專案小組版")),
    ("B-2", ("B-2", "B－2", "幹事會複審", "幹事會")),
    ("C", ("審議會版",)),
    ("D", ("核定版",)),
]


def _int_value_right(label: dict, dets: List[dict]) -> Optional[int]:
    """label 右側值，強制轉為合理範圍內的整數車位數，否則 None。"""
    val = _coerce("actual_parking", _nearest_value_right(label, dets))
    if isinstance(val, int) and 0 <= val <= _PARKING_MAX:
        return val
    return None


def _surface_mech(dets: List[dict]) -> tuple:
    """實設汽車停車位的 平面 / 機械 子項值。需頁面有「實設」錨點，避免誤配。
    回傳 (平面, 機械)，取不到者為 None。"""
    if not any("實設" in (d.get("text") or "") for d in dets):
        return None, None
    surface = mech = None
    for d in dets:
        text = (d.get("text") or "").strip()
        if surface is None and "平面" in text:
            surface = _int_value_right(d, dets)
        if mech is None and "機械" in text:
            mech = _int_value_right(d, dets)
    return surface, mech


def _checked_code(text: str) -> Optional[str]:
    """文字中『實心勾選記號緊鄰類別碼/敘述』→ 該類別碼（勾選框表單，如合家歡 ■B-1）。"""
    for code, keys in _SUBMISSION_TYPE_KEYS:
        for kw in keys:
            start = 0
            while True:
                idx = text.find(kw, start)
                if idx == -1:
                    break
                if any(m in text[max(0, idx - 5):idx] for m in _FILLED_MARKS):
                    return code
                start = idx + 1
    return None


def _detect_submission_type(dets: List[dict]) -> Optional[str]:
    """判送審類別，以「送審類別」標籤為錨、取同列右側的值：
    1. 勾選框表單（合家歡 ■B-1）：實心記號緊鄰類別 → 該碼。
    2. 文字值表單（大魯閣「(第1次)審議會版(第1次補正)」）：右側只出現單一版次敘述
       → 採用。若同時出現多個版次敘述（各選項都印在同列且無勾記）→ 回 None，維持
       人工確認，不亂猜。
    """
    geo = [d for d in dets if "yc" in d and "x0" in d and "x1" in d]
    label = next((d for d in geo if "送審類別" in (d.get("text") or "")), None)
    if label is None:
        return None
    right = _same_row_right(label, geo)
    text = "".join((d.get("text") or "") for d in right)

    checked = _checked_code(text)
    if checked:
        return checked

    matched = {code for code, keys in _SUBMISSION_TYPE_KEYS if any(k in text for k in keys)}
    return next(iter(matched)) if len(matched) == 1 else None


def reconstruct_fields(dets: List[dict]) -> Dict[str, object]:
    """Map OCR detections to review-table fields.

    Numeric fields come from row geometry (label→nearest-right value). Two
    審議資料表-specific補強：實設汽車停車位以 平面+機械 加總補出；送審類別以勾選框
    判讀。Never raises on odd/empty input.
    """
    # 勾選框判讀用「全部」偵測文字（實心記號可能無幾何資訊）。
    submission_type = _detect_submission_type(dets)

    # 幾何欄位只能用帶座標的偵測。
    geo_dets = [d for d in dets if "yc" in d and "x0" in d and "x1" in d]

    result: Dict[str, object] = {}
    for det in geo_dets:
        text = (det.get("text") or "").strip()
        if not text:
            continue
        for keywords, field, excludes in _iter_label_map():
            if field in result or field not in _NUMERIC_FIELDS:
                continue
            if _label_matches(text, keywords, excludes):
                raw = _nearest_value_right(det, dets)
                if raw is None:
                    m = _TRAILING_NUM_RE.search(text)
                    raw = m.group(1) if m else None
                value = _coerce(field, raw) if raw is not None else None
                if value is not None:
                    result[field] = value
                break

    # 實設汽車停車位 = 平面 + 機械 + 無障礙 + 充電（依審議慣例四項相加；充電空白計 0）。
    # 優先於主迴圈可能抓到的單一子格值；並附分項明細供報告呈現。
    surface, mech = _surface_mech(geo_dets)
    if surface is not None and mech is not None:
        acc = result.get("accessible_parking") or 0
        ev = result.get("ev_parking")
        charge = ev or 0
        result["actual_parking"] = surface + mech + acc + charge
        ev_disp = str(ev) if ev is not None else "-"
        result["actual_parking_detail"] = (
            f"平面{surface}機械{mech}無障礙{acc}充電{ev_disp}"
        )

    if submission_type:
        result["submission_type"] = submission_type
    return result
