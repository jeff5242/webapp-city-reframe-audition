from __future__ import annotations

import re
from typing import List

from ..models import NumberContext, WrongTermMatch
from ..parsers.page_text import pages_text

# Wrong term → correct term mapping for urban renewal documents
WRONG_TERMS: dict = {
    # 法定用詞錯誤
    "計劃書": "計畫書",       # 台灣正式公文：計畫，非計劃
    "計劃案": "計畫案",
    "協議合件": "協議合建",    # 常見錯字
    "更新單位": "更新單元",    # 法定用詞：更新單元
    "都市更新條列": "都市更新條例",  # 條例非條列
    "公開展示": "公開展覽",    # 法定程序名稱：公開展覽
    "容積率獎勵": "容積獎勵",  # 正確：容積獎勵（非容積率獎勵）
    "申請報備": "申請報核",    # 正確程序：報核
    "審儀": "審議",            # 錯別字
    "聽証": "聽證",            # 繁體正確：聽證
    "証明": "證明",
    "法定地價": "公告地價",    # 法定用詞
    "都市更新審議会": "都市更新審議會",  # 简体混入
    "权利变换": "權利變換",    # 简体混入
    "实施者": "實施者",        # 简体混入
}

# Patterns to extract key numbers from main text for cross-reference
_NUMBER_PATTERNS: List[tuple] = [
    (
        "accessible_parking",
        re.compile(r"無障礙[停車位\s（(]*(\d+)\s*輛"),
    ),
    (
        "legal_parking",
        re.compile(r"法定[汽車]*停車位[^\n]*\n?(\d+)\s*輛"),
    ),
    (
        "bonus_floor_area",
        re.compile(r"合計獎勵樓地板面積[^\d]*(\d[\d,，.]+)"),
    ),
    (
        "ev_parking",
        re.compile(r"充電(\d+)輛"),
    ),
]


def _context(text: str, start: int, end: int, window: int = 30) -> str:
    return text[max(0, start - window): end + window].replace("\n", " ").strip()


def scan_for_wrong_terms(pdf_path: str, max_pages: int = 40) -> List[WrongTermMatch]:
    """Scan PDF pages for wrong/inconsistent terminology."""
    pages = pages_text(pdf_path, 1, max_pages)
    matches: List[WrongTermMatch] = []

    for page in pages:
        text = page["text"]
        page_num = page["page_num"]
        for wrong, correct in WRONG_TERMS.items():
            idx = text.find(wrong)
            if idx != -1:
                matches.append(WrongTermMatch(
                    page=page_num,
                    wrong_term=wrong,
                    correct_term=correct,
                    context=_context(text, idx, idx + len(wrong)),
                ))

    return matches


def extract_number_contexts(pdf_path: str, start_page: int = 10, end_page: int = 60) -> List[NumberContext]:
    """Extract key numbers from main text for consistency cross-check."""
    pages = pages_text(pdf_path, start_page, end_page)
    contexts: List[NumberContext] = []

    for page in pages:
        text = page["text"]
        page_num = page["page_num"]
        for field_name, pattern in _NUMBER_PATTERNS:
            for m in pattern.finditer(text):
                raw_num = m.group(1).replace(",", "").replace("，", "")
                try:
                    value = float(raw_num)
                except ValueError:
                    continue
                contexts.append(NumberContext(
                    page=page_num,
                    field=field_name,
                    value=value,
                    raw_text=_context(text, m.start(), m.end()),
                ))

    return contexts
