"""標註頁截圖（副總 UX）：把已標紅框的 PDF 頁直接 raster 成 base64 圖，內嵌報告。

審核者不必再下載 PDF 再開啟——問題頁的紅框預覽就在報告裡（零點擊）。
純地端 PyMuPDF 光柵化，無網路 / 無 LLM，符合主權模式。任何失敗一律回空 dict，
讓報告優雅降級成純文字頁碼跳轉連結，絕不因截圖失敗而中斷審查。
"""
from __future__ import annotations

import base64
from typing import Dict, Iterable

# 光柵化參數：A4 在 zoom=1.2 約 714×1009 px，JPEG(q75) 每頁約數十 KB，
# 內嵌報告不致爆量；上限 max_pages 防止大量發現時報告過肥。
_ZOOM = 1.2
_JPEG_QUALITY = 72
_MAX_PAGES = 12


def _encode_page(page, zoom: float = _ZOOM) -> str:
    """把單頁光柵化成 data URI（JPEG 優先，退回 PNG）。"""
    import fitz  # local import：缺 PyMuPDF 時由呼叫端捕捉

    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    try:
        raw = pix.tobytes("jpeg", jpg_quality=_JPEG_QUALITY)
        mime = "image/jpeg"
    except Exception:
        raw = pix.tobytes("png")
        mime = "image/png"
    return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")


def render_evidence_thumbnails(
    pdf_bytes: bytes,
    pages: Iterable[int],
    zoom: float = _ZOOM,
    max_pages: int = _MAX_PAGES,
) -> Dict[int, str]:
    """把標註 PDF 指定頁（1-based）光柵化成 {page: data-URI}，供報告內嵌預覽。

    pages 去重排序後最多取 max_pages 頁；超出頁數範圍或個別頁失敗都略過。
    任何頂層失敗（缺 PyMuPDF、壞 bytes）回 {}，呼叫端據此降級。
    """
    if not pdf_bytes:
        return {}
    try:
        import fitz
    except ImportError:
        return {}

    wanted = sorted({p for p in pages if isinstance(p, int) and p > 0})[:max_pages]
    if not wanted:
        return {}

    out: Dict[int, str] = {}
    doc = None
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        for page_num in wanted:
            if page_num > doc.page_count:
                continue
            try:
                out[page_num] = _encode_page(doc[page_num - 1], zoom)
            except Exception:
                continue
        return out
    except Exception:
        return {}
    finally:
        if doc is not None:
            doc.close()
