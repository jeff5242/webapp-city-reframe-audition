from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RegulationVersion:
    label: str          # e.g. "111年版"
    effective_date: str # ISO format "YYYY-MM-DD"
    end_date: str       # ISO format "YYYY-MM-DD" or "" for latest


# Versions ordered from newest to oldest
_VERSIONS = [
    RegulationVersion("113年版", "2024-12-03", ""),
    RegulationVersion("111年版", "2022-03-24", "2024-12-02"),
    RegulationVersion("108年版", "2019-01-30", "2022-03-23"),
    RegulationVersion("107年版", "2018-03-23", "2019-01-29"),
]

_DEFAULT_VERSION = _VERSIONS[1]  # 111年版 as fallback


def _parse_roc_date(date_str: str) -> Optional[str]:
    """Convert ROC date like '113年9月2日' to ISO '2024-09-02'."""
    m = re.search(r'(\d+)\s*年\s*(\d+)\s*月\s*(\d+)\s*日', date_str)
    if not m:
        return None
    try:
        year = int(m.group(1)) + 1911
        month = int(m.group(2))
        day = int(m.group(3))
        return f"{year:04d}-{month:02d}-{day:02d}"
    except ValueError:
        return None


def select_version(fill_date: Optional[str]) -> tuple:
    """
    Given a fill_date string (ROC format), return (RegulationVersion, iso_date_or_None).
    Falls back to 111年版 if date is unparseable.
    """
    if not fill_date:
        return _DEFAULT_VERSION, None

    iso = _parse_roc_date(fill_date)
    if not iso:
        return _DEFAULT_VERSION, None

    for version in _VERSIONS:
        if iso >= version.effective_date:
            return version, iso

    return _VERSIONS[-1], iso  # older than 107年版, use oldest
