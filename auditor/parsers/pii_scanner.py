from __future__ import annotations

import re
from typing import List, Dict, Any

from ..models import PiiRisk

# Landline: (02)1234-5678 or (02) 1234-5678
_PHONE_LANDLINE = re.compile(r'\(0\d{1,2}\)\s*\d{3,4}-?\d{4}')
# Mobile: 0912-345-678 or 0912345678
_PHONE_MOBILE = re.compile(r'09\d{2}-?\d{3}-?\d{3}')
# Taiwan National ID
_ID_NUMBER = re.compile(r'[A-Z]\d{9}')
# Residential address: contains 巷...弄...號 pattern (lane-alley-number)
_RESIDENTIAL_ADDRESS = re.compile(r'巷.{0,15}弄.{0,8}號')


def _context(text: str, start: int, end: int, window: int = 25) -> str:
    return text[max(0, start - window): end + window].replace('\n', ' ').strip()


def scan_page(page_num: int, text: str) -> List[PiiRisk]:
    risks: List[PiiRisk] = []

    for match in _PHONE_LANDLINE.finditer(text):
        risks.append(PiiRisk(
            page=page_num,
            risk_type="phone",
            value=match.group(),
            context=_context(text, match.start(), match.end()),
            severity="MEDIUM",
        ))

    for match in _PHONE_MOBILE.finditer(text):
        risks.append(PiiRisk(
            page=page_num,
            risk_type="phone",
            value=match.group(),
            context=_context(text, match.start(), match.end()),
            severity="MEDIUM",
        ))

    for match in _RESIDENTIAL_ADDRESS.finditer(text):
        risks.append(PiiRisk(
            page=page_num,
            risk_type="residential_address",
            value=match.group(),
            context=_context(text, match.start(), match.end(), window=40),
            severity="HIGH",
        ))

    for match in _ID_NUMBER.finditer(text):
        risks.append(PiiRisk(
            page=page_num,
            risk_type="id_number",
            value=match.group(),
            context=_context(text, match.start(), match.end()),
            severity="HIGH",
        ))

    return risks


def scan_pages(pages: List[Dict[str, Any]]) -> List[PiiRisk]:
    all_risks: List[PiiRisk] = []
    for page in pages:
        all_risks.extend(scan_page(page["page_num"], page["text"]))
    return all_risks
