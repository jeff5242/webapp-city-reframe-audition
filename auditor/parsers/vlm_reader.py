"""On-prem VLM OCR client for the 審議資料表 (地端 OCR).

When ``VLM_ENDPOINT`` is set, review-table recognition can call an on-prem,
open-weight VLM inference service (OpenAI-compatible ``/v1/chat/completions`` —
e.g. Qwen2.5-VL served by vLLM / LLaMA-Factory) that reads the page image and
returns the structured fields directly. On dense scanned tables this is far
faster and more accurate than the CPU PaddleOCR tiers — turning an ``/audit``
that times out into one that returns in seconds.

Sovereign: the endpoint is an on-prem GPU service on the機關 network; no
document data leaves the machine. Degrades gracefully — when ``VLM_ENDPOINT``
is unset or anything fails, :func:`extract_review_table_fields` returns ``{}``
and the caller falls back to the existing OCR tiers.

Config (env):
    VLM_ENDPOINT  base URL of the inference service (required to enable).
                  Either ``http://host:8000`` or a full ``.../v1/chat/completions``.
    VLM_MODEL     served model name (default ``default``).
    VLM_TIMEOUT   request timeout in seconds (default ``60``).
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import urllib.request
from typing import Dict, Optional

log = logging.getLogger(__name__)

# 上傳圖壓到長邊上限:整頁高解析掃描會讓 VLM 推論變慢;2000px 已足夠讀清欄位
# (與 ocr_reader._OCR_MAX_DIM 一致的實測結論)。
_MAX_EDGE = 2000
_RENDER_ZOOM = 3.0
_DEFAULT_MODEL = "default"
_DEFAULT_TIMEOUT = 60.0
_MAX_TOKENS = 1024

# 要 VLM 輸出的欄位(鍵名對齊 ReviewTableData,直接可 merge)。
_PROMPT = (
    "這是臺北市都市更新審議資料表的頁面影像。請逐格判讀,只輸出一個 JSON 物件,"
    "包含下列鍵(找不到的填 null,數值保留數字、可含單位):\n"
    "land_area(基地面積 m²)、base_floor_area(基準容積 m²)、"
    "bonus_floor_area(獎勵樓地板面積合計 m²)、bonus_limit(容積獎勵上限 m²)、"
    "legal_parking(法定汽車停車位 輛)、actual_parking(實設汽車停車位 輛)、"
    "accessible_parking(無障礙停車位 輛)、ev_parking(充電車位 輛)、"
    "implementer(實施者名稱)、submission_type(送審類別,只填代碼 A-1/B-1/B-2/C/D)、"
    "fill_date(填表日期 民國年月日)、"
    "report_filing_date(報核日期:辦理過程表中「…計畫報核」那一列的日期,取最新一筆,民國年月日)、"
    "owner_consent_ratio(土地所有權人同意比率 %)、case_name(計畫名稱)。\n"
    "只輸出 JSON,不要多餘文字。"
)

_SUBMISSION_PREFIXES = ("A-1", "B-1", "B-2")


def vlm_enabled() -> bool:
    """True when an on-prem VLM endpoint is configured."""
    return bool(os.getenv("VLM_ENDPOINT", "").strip())


def _endpoint() -> str:
    base = os.getenv("VLM_ENDPOINT", "").strip().rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return base + "/v1/chat/completions"


def _normalize_submission_type(value: object) -> object:
    """Reduce a送審類別 like 「A-1:公開展覽」to its leading code 「A-1」.

    Leaves the value untouched when it does not start with a known prefix, so a
    genuinely different string is never silently mangled.
    """
    s = str(value).strip()
    for code in _SUBMISSION_PREFIXES:
        if s.startswith(code):
            return code
    return value


def _render_page_jpeg_b64(pdf_path: str, page_num: int) -> Optional[str]:
    """Render a 1-based page to a size-capped JPEG, base64-encoded; None on error."""
    try:
        import fitz
        from PIL import Image
    except ImportError:
        return None
    try:
        doc = fitz.open(pdf_path)
        try:
            if page_num < 1 or page_num > len(doc):
                return None
            pix = doc[page_num - 1].get_pixmap(
                matrix=fitz.Matrix(_RENDER_ZOOM, _RENDER_ZOOM)
            )
            png = pix.tobytes("png")
        finally:
            doc.close()
        img = Image.open(io.BytesIO(png)).convert("RGB")
        w, h = img.size
        scale = _MAX_EDGE / max(w, h)
        if scale < 1:
            img = img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        return base64.standard_b64encode(buf.getvalue()).decode("ascii")
    except Exception as exc:  # pragma: no cover - defensive; render is best-effort
        log.warning("VLM page render failed for page %d: %s", page_num, exc)
        return None


def _load_json_obj(raw: str) -> Optional[dict]:
    """Tolerate ```json fences / surrounding prose; return the first JSON object."""
    stripped = re.sub(
        r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE
    ).strip()
    for cand in (raw, stripped):
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    return None


def _post_chat(b64: str, prompt: str, max_tokens: int) -> Optional[str]:
    """POST an image+prompt to the VLM; return the assistant text, or None on error."""
    model = os.getenv("VLM_MODEL", _DEFAULT_MODEL)
    try:
        timeout = float(os.getenv("VLM_TIMEOUT", _DEFAULT_TIMEOUT))
    except ValueError:
        timeout = _DEFAULT_TIMEOUT
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": max_tokens,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                {"type": "text", "text": prompt},
            ],
        }],
    }
    try:
        req = urllib.request.Request(
            _endpoint(),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.load(resp)
        return body["choices"][0]["message"]["content"]
    except Exception as exc:
        log.warning("VLM request failed (non-fatal): %s", exc)
        return None


# 整頁轉錄(逐頁文字供給器的掃描頁引擎)。一整頁文字 token 較多,上限放大。
_TRANSCRIBE_PROMPT = (
    "請把這張頁面影像裡的所有文字,逐行、按閱讀順序完整轉錄出來。"
    "只輸出文字本身,不要翻譯、不要說明、不要加任何標記。"
)
_TRANSCRIBE_MAX_TOKENS = 4096


def transcribe_page(pdf_path: str, page_num: int) -> Optional[str]:
    """VLM 逐頁 OCR:把整頁影像轉錄成文字。VLM 未啟用或失敗回 None。"""
    if not vlm_enabled():
        return None
    b64 = _render_page_jpeg_b64(pdf_path, page_num)
    if not b64:
        return None
    return _post_chat(b64, _TRANSCRIBE_PROMPT, _TRANSCRIBE_MAX_TOKENS)


def extract_review_table_fields(pdf_path: str, page_num: int) -> Dict[str, object]:
    """Extract review-table fields via the on-prem VLM. ``{}`` when disabled/failed.

    Returns a dict of ReviewTableData field names → raw values (non-null only).
    The caller (:func:`table_extractor.enhance_review_table`) coerces types and
    merges, never overwriting a value the text pass already found.
    """
    if not vlm_enabled():
        return {}
    b64 = _render_page_jpeg_b64(pdf_path, page_num)
    if not b64:
        return {}

    content = _post_chat(b64, _PROMPT, _MAX_TOKENS)
    if content is None:
        return {}

    obj = _load_json_obj(content)
    if not obj:
        log.warning("VLM returned unparseable content")
        return {}

    fields: Dict[str, object] = {k: v for k, v in obj.items() if v is not None}
    if "submission_type" in fields:
        fields["submission_type"] = _normalize_submission_type(fields["submission_type"])
    return fields
