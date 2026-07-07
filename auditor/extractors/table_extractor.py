"""Hybrid structured extraction for the 審議資料表 (review table).

The review table is a dense bordered grid where each visual row holds several
label:value pairs across columns. Flattening it with line-based OCR destroys the
label→value adjacency, so plain regex over OCR text is unstable. This module
recovers structure with a two-tier, sovereignty-aware strategy:

1. **On-prem** — PaddleOCR PP-Structure table recognition yields cell grids;
   `_map_structured_cells()` maps each label cell to its value cell. No data
   leaves the machine.
2. **Escalation (cloud)** — only when on-prem coverage of the critical fields is
   insufficient, the page image is sent to Claude vision with a strict tool
   schema. Mirrors the evidence-grounding tiering (cheap/local first, escalate).

All steps degrade gracefully: missing deps, no API key, or an error yields no
change rather than raising. The merge only *fills* missing fields on the base
`ReviewTableData`, never overwrites a value already found by the text pass.
"""
from __future__ import annotations

import base64
import logging
import os
import re
from dataclasses import replace
from typing import Dict, List, Optional

from ..models import ReviewTableData

log = logging.getLogger(__name__)

_DEFAULT_VISION_MODEL = "claude-sonnet-4-6"  # vision quality matters for dense tables
_MAX_TOKENS = 1500
_RENDER_ZOOM = 3.0  # high DPI for a text-dense form

# Fields the audit rules most depend on; coverage over these decides escalation.
_CRITICAL_FIELDS = (
    "bonus_floor_area",
    "bonus_limit",
    "legal_parking",
    "actual_parking",
    "land_area",
)

# Numeric fields (parsed as float) vs integer fields.
_FLOAT_FIELDS = {"land_area", "base_floor_area", "bonus_floor_area", "bonus_limit", "owner_consent_ratio"}
_INT_FIELDS = {"legal_parking", "actual_parking", "accessible_parking", "ev_parking"}


# ── numeric parsing ───────────────────────────────────────────────────────────

def _to_float(raw) -> Optional[float]:
    if raw is None:
        return None
    cleaned = re.sub(r"[,，\s平方公尺m²㎡%]", "", str(raw))
    try:
        return float(cleaned)
    except ValueError:
        return None


def _to_int(raw) -> Optional[int]:
    if raw is None:
        return None
    digits = re.sub(r"[^\d]", "", str(raw))
    return int(digits) if digits else None


def _coerce(field: str, raw):
    if field in _FLOAT_FIELDS:
        return _to_float(raw)
    if field in _INT_FIELDS:
        return _to_int(raw)
    if raw is None:
        return None
    s = str(raw).strip()
    return s or None


# ── coverage + merge (pure) ───────────────────────────────────────────────────

def _coverage(fields: Dict[str, object], keys=_CRITICAL_FIELDS) -> float:
    """Fraction of *keys* present (non-None) in *fields*."""
    if not keys:
        return 1.0
    present = sum(1 for k in keys if fields.get(k) is not None)
    return present / len(keys)


def _merge_into(base: ReviewTableData, fields: Dict[str, object]) -> ReviewTableData:
    """Return a copy of *base* with any missing field filled from *fields*.

    Never overwrites a value the text pass already found — the text regex is
    trusted when it produced a value; structured extraction only fills gaps.
    """
    updates = {}
    for key, raw in fields.items():
        if not hasattr(base, key):
            continue
        if getattr(base, key) is not None:
            continue  # keep existing
        value = _coerce(key, raw)
        if value is not None:
            updates[key] = value
    return replace(base, **updates) if updates else base


# ── PP-Structure cell mapping (pure) ──────────────────────────────────────────

# label keyword(s) → field. Order matters: more specific labels first so that
# e.g. "實設汽車停車位" is matched before a looser "停車位".
_LABEL_FIELD_MAP: List[tuple] = [
    (("實設汽車停車位", "實設停車位"), "actual_parking"),
    (("法定汽車停車位", "法定停車位"), "legal_parking"),
    (("無障礙停車位", "無障礙"), "accessible_parking"),
    (("充電車位", "充電"), "ev_parking"),
    (("基地面積",), "land_area"),
    (("基準容積",), "base_floor_area"),
    (("獎勵樓地板面積合計", "合計獎勵樓地板面積", "獎勵面積合計", "獎勵合計"), "bonus_floor_area"),
    (("容積獎勵上限", "獎勵上限"), "bonus_limit"),
    (("土地所有權人同意比率", "所有權人同意"), "owner_consent_ratio"),
    (("實施者",), "implementer"),
    (("填表日期",), "fill_date"),
]


def _map_structured_cells(cells: List[dict]) -> Dict[str, object]:
    """Map PP-Structure cells to fields by label→adjacent-value-cell.

    *cells* is a list of {"row": int, "col": int, "text": str}. For each label
    keyword found in a cell, the value is taken from the cell immediately to the
    right (same row, next col); if that is empty, the trailing number inside the
    label cell itself is used.
    """
    by_pos = {(c["row"], c["col"]): (c.get("text") or "").strip() for c in cells}
    result: Dict[str, object] = {}

    for cell in cells:
        text = (cell.get("text") or "").strip()
        if not text:
            continue
        for keywords, field in _LABEL_FIELD_MAP:
            if field in result:
                continue
            if any(kw in text for kw in keywords):
                right = by_pos.get((cell["row"], cell["col"] + 1), "")
                value = right or text
                result[field] = value
                break
    return result


# PP-Structure is disabled by default: the bundled PaddleOCR build is unreliable
# on this stack (layout lang, tensor-dim, and zlib errors observed on real docs)
# and only wastes time + logs noise before falling through to the vision path.
# Set ENABLE_PPSTRUCTURE=1 to re-enable once a working build is validated.
def _ppstructure_enabled() -> bool:
    return os.getenv("ENABLE_PPSTRUCTURE", "").lower() in ("1", "true", "yes")


def _extract_via_ppstructure(pdf_path: str, page_num: int) -> Dict[str, object]:
    """Best-effort on-prem structured extraction via PaddleOCR PP-Structure.

    Disabled unless ENABLE_PPSTRUCTURE is set. Returns {} when disabled,
    unavailable, or on any failure (degrades to the vision path).
    """
    if not _ppstructure_enabled():
        return {}
    try:
        from paddleocr import PPStructure  # type: ignore
    except Exception:
        log.debug("PP-Structure unavailable; skipping on-prem table recognition")
        return {}

    try:
        import numpy as np
        from PIL import Image
        import io as _io

        png = _render_page_png(pdf_path, page_num)
        if not png:
            return {}
        img = np.array(Image.open(_io.BytesIO(png)))

        engine = _get_ppstructure(PPStructure)
        results = engine(img)
        cells = _ppstructure_to_cells(results)
        return _map_structured_cells(cells)
    except BaseException as exc:
        # BaseException (not just Exception): PaddleOCR can call sys.exit() on
        # unsupported config (e.g. layout lang), which raises SystemExit and
        # would otherwise crash the whole /audit. Degrade to vision instead.
        log.warning("PP-Structure extraction failed (non-fatal): %s", exc)
        return {}


_ppstructure_engine = None


def _get_ppstructure(cls):
    global _ppstructure_engine
    if _ppstructure_engine is None:
        # Layout models only support 'ch'/'en' (NOT 'chinese_cht'); 'ch' handles
        # Chinese table structure. Cell-text quality is secondary — the grid is
        # what we need, and vision fallback covers accuracy.
        _ppstructure_engine = cls(show_log=False, lang="ch")
    return _ppstructure_engine


def _ppstructure_to_cells(results) -> List[dict]:
    """Convert PP-Structure output to a flat list of {row, col, text} cells.

    PP-Structure returns table regions with an HTML representation; we parse the
    HTML grid into positional cells. Non-table regions are ignored.
    """
    cells: List[dict] = []
    for region in results or []:
        if (region.get("type") if isinstance(region, dict) else None) != "table":
            continue
        html = region.get("res", {}).get("html", "") if isinstance(region, dict) else ""
        cells.extend(_html_table_to_cells(html))
    return cells


def _html_table_to_cells(html: str) -> List[dict]:
    """Parse a simple HTML table into {row, col, text} cells (no colspan logic)."""
    cells: List[dict] = []
    rows = re.findall(r"<tr>(.*?)</tr>", html or "", flags=re.DOTALL)
    for r, row_html in enumerate(rows):
        tds = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, flags=re.DOTALL)
        for c, cell_html in enumerate(tds):
            text = re.sub(r"<[^>]+>", "", cell_html).strip()
            cells.append({"row": r, "col": c, "text": text})
    return cells


# ── Vision escalation (cloud) ─────────────────────────────────────────────────

_VISION_TOOL = {
    "name": "extract_review_table",
    "description": "從臺北市都市更新審議資料表的頁面影像中，抽取結構化欄位。找不到的欄位回傳 null。",
    "input_schema": {
        "type": "object",
        "properties": {
            "land_area": {"type": ["number", "null"], "description": "基地面積 (m²)"},
            "base_floor_area": {"type": ["number", "null"], "description": "基準容積 (m²)"},
            "bonus_floor_area": {"type": ["number", "null"], "description": "獎勵樓地板面積合計 (m²)"},
            "bonus_limit": {"type": ["number", "null"], "description": "容積獎勵上限 (m²)"},
            "legal_parking": {"type": ["integer", "null"], "description": "法定汽車停車位 (輛)"},
            "actual_parking": {"type": ["integer", "null"], "description": "實設汽車停車位 (輛)"},
            "accessible_parking": {"type": ["integer", "null"], "description": "無障礙停車位 (輛)"},
            "ev_parking": {"type": ["integer", "null"], "description": "充電車位 (輛)"},
            "implementer": {"type": ["string", "null"], "description": "實施者名稱"},
            "submission_type": {"type": ["string", "null"], "description": "送審類別 (A-1/B-1/B-2/C/D)"},
            "fill_date": {"type": ["string", "null"], "description": "填表日期 (民國年月日)"},
            "report_filing_date": {
                "type": ["string", "null"],
                "description": (
                    "報核日期：辦理過程表中「申請事業計畫報核」或「申請權利變換計畫報核」"
                    "那一列的日期，取最新一筆。格式為民國年月日 (如 112年9月8日)。"
                ),
            },
            "owner_consent_ratio": {"type": ["number", "null"], "description": "土地所有權人同意比率 (%)"},
        },
        "required": [],
    },
}


def _parse_vision_fields(raw: dict) -> Dict[str, object]:
    """Keep only non-null values from a vision tool_use payload."""
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if v is not None}


def _extract_via_vision(pdf_path: str, page_num: int, model: str = _DEFAULT_VISION_MODEL) -> Dict[str, object]:
    """Escalation: send the page image to Claude vision for structured extraction.

    Returns {} when no API key, SDK missing, or any error.
    """
    if not os.getenv("ANTHROPIC_API_KEY"):
        return {}
    try:
        import anthropic
    except ImportError:
        return {}

    try:
        png = _render_page_png(pdf_path, page_num)
        if not png:
            return {}
        b64 = base64.standard_b64encode(png).decode("ascii")

        client = anthropic.Anthropic()
        response = client.messages.create(
            model=model,
            max_tokens=_MAX_TOKENS,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": b64},
                    },
                    {
                        "type": "text",
                        "text": "這是臺北市都市更新審議資料表。請逐格判讀，抽取指定欄位的數值。",
                    },
                ],
            }],
            tools=[_VISION_TOOL],
            tool_choice={"type": "tool", "name": "extract_review_table"},
        )
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "extract_review_table":
                return _parse_vision_fields(block.input)
        return {}
    except Exception as exc:
        log.error("Vision extraction failed: %s", exc)
        return {}


# ── page render ───────────────────────────────────────────────────────────────

def _render_page_png(pdf_path: str, page_num: int, zoom: float = _RENDER_ZOOM) -> Optional[bytes]:
    """Render a 1-based page to PNG bytes via PyMuPDF; None on failure."""
    try:
        import fitz
    except ImportError:
        return None
    try:
        doc = fitz.open(pdf_path)
        try:
            if page_num < 1 or page_num > len(doc):
                return None
            pix = doc[page_num - 1].get_pixmap(matrix=fitz.Matrix(zoom, zoom))
            return pix.tobytes("png")
        finally:
            doc.close()
    except Exception as exc:
        log.warning("Page render failed for page %d: %s", page_num, exc)
        return None


# ── on-prem geometric bbox reconstruction (副總 #3) ───────────────────────────

def _reconstruct_via_bbox(pdf_path: str, page_num: int) -> Dict[str, object]:
    """On-prem: reconstruct numeric fields from OCR bbox geometry.

    Sovereign — no API key, no extra model. Returns {} when OCR is unavailable
    or on any failure (degrades to the vision path just like PP-Structure).
    """
    try:
        from ..parsers.ocr_reader import ocr_page_boxes
        from .table_reconstruct import reconstruct_fields
    except Exception:
        return {}
    try:
        dets = ocr_page_boxes(pdf_path, page_num - 1)  # raw_page is 1-based
        return reconstruct_fields(dets)
    except Exception as exc:
        log.warning("bbox reconstruction failed (non-fatal): %s", exc)
        return {}


# ── orchestration ─────────────────────────────────────────────────────────────

def enhance_review_table(
    pdf_path: str,
    base: ReviewTableData,
    coverage_threshold: float = 0.6,
) -> ReviewTableData:
    """Fill gaps in *base* using hybrid structured extraction.

    Tiers (each only *fills* missing fields, never overwrites the text pass):
    1. On-prem PP-Structure table recognition (disabled by default).
    2. On-prem geometric bbox reconstruction from OCR (副總 #3) — the primary
       sovereign gap-filler for the dense grid; runs whenever critical-field
       coverage is still incomplete.
    3. Claude vision — only when coverage stays below *coverage_threshold* and an
       API key is present (off in sovereign mode).

    Returns *base* unchanged if nothing could be improved.
    """
    page_num = base.raw_page
    if not page_num:
        return base

    structured = _extract_via_ppstructure(pdf_path, page_num)
    merged = _merge_into(base, structured)

    # Tier 2 — geometric bbox reconstruction fills any remaining critical gaps.
    current = {f: getattr(merged, f) for f in _CRITICAL_FIELDS}
    if _coverage(current) < 1.0:
        geo = _reconstruct_via_bbox(pdf_path, page_num)
        merged = _merge_into(merged, geo)

    # Tier 3 — cloud vision escalation (needs API key; off in sovereign mode).
    current = {f: getattr(merged, f) for f in _CRITICAL_FIELDS}
    if _coverage(current) < coverage_threshold:
        vision = _extract_via_vision(pdf_path, page_num)
        if vision:
            merged = _merge_into(merged, vision)

    return merged
