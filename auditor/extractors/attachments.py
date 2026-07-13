"""附錄偵測（供 playbook 附錄必附規則使用）。

從計畫書前段/目錄文字，偵測 111 年版 24 項附錄中哪些已檢附。
純文字啟發式（比對名稱關鍵字），與既有 front_docs 偵測同風格；偵測不到回 None
→ 規則 skip，不誤報缺件。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

# canonical 名稱 → 比對關鍵字（任一出現即視為存在）
KNOWN_ATTACHMENTS: Dict[str, List[str]] = {
    "實施者證明文件": ["實施者證明"],
    "更新單元核准函": ["更新單元核准函", "核准函"],
    "更新單元土地權屬清冊": ["土地權屬清冊"],
    "更新單元合法建築物權屬清冊": ["合法建築物權屬清冊"],
    "建築工程建材設備等級表": ["建材設備等級表", "建材設備"],
    "住戶管理規約": ["住戶管理規約", "管理規約"],
    "公辦公聽會相關資料": ["公辦公聽會", "自辦公聽會", "公聽會"],
    "聽證相關資料": ["聽證"],
    "都市更新事業計畫圖": ["事業計畫圖"],
    "不動產估價報告書摘要": ["不動產估價報告", "估價報告書"],
    "交通影響評估報告書摘要": ["交通影響評估"],
}

# 111 年版標示「必附」的附錄（缺件應提示）
REQUIRED_ATTACHMENTS: Tuple[str, ...] = (
    "實施者證明文件",
    "更新單元核准函",
    "更新單元土地權屬清冊",
    "建築工程建材設備等級表",
    "住戶管理規約",
    "公辦公聽會相關資料",
    "聽證相關資料",
    "都市更新事業計畫圖",
)


def detect_attachments(text: str) -> Tuple[str, ...]:
    """從文字偵測已檢附的附錄名稱（canonical）。純函式，易測。"""
    if not text:
        return tuple()
    found = [name for name, kws in KNOWN_ATTACHMENTS.items() if any(kw in text for kw in kws)]
    return tuple(found)


def detect_attachments_from_pdf(pdf_path: str, max_pages: int = 20) -> Optional[Tuple[str, ...]]:
    """掃描 PDF 前段（含目錄）偵測附錄。失敗回 None（→ 規則 skip 不誤報）。"""
    try:
        from ..parsers.pdf_reader import extract_pages_text
        pages = extract_pages_text(pdf_path, 1, max_pages, ocr_image_pages=False)
        text = "\n".join(p.get("text", "") for p in pages)
        if not text.strip():
            return None
        return detect_attachments(text)
    except Exception:
        return None
