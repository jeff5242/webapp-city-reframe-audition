"""欄位值正規化與寬鬆比對（③ 後處理校正層）。

VLM 零樣本 51% 的「錯」有相當比例其實不是讀錯，而是**格式差異**：全半形、
千分位、「臺／台」、民國／西元、面積單位寫法。本層以確定性規則把這些差異吸收掉，
用於：
- 比對「模型輸出 vs gold」時的寬鬆判定（recover 分數）。
- 產出報告前把欄位值正規化成一致寫法（格式一致性）。

全為純函式、無副作用、易測。不猜、不臆造——只做規則化轉換。
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

# 「臺/台」在正式公文統一用「臺」；比對時兩者視為相同。
_TAI_VARIANTS = str.maketrans({"台": "臺"})

# 面積單位的各種寫法 → 正規化為 m2
_AREA_UNIT_RE = re.compile(r"(㎡|平方公尺|平方米|m²|m2|M²|M2)")

_NUM_RE = re.compile(r"-?\d[\d,，]*(?:\.\d+)?")

# 民國年 → 西元轉換基準
_ROC_OFFSET = 1911


def normalize_text(text: Optional[str]) -> str:
    """NFKC（全形→半形、相容字元）+ 去空白 + 臺台統一。空值回空字串。"""
    if not text:
        return ""
    norm = unicodedata.normalize("NFKC", text)
    norm = norm.replace(" ", "").replace("　", "")
    return norm.translate(_TAI_VARIANTS)


def normalize_area_unit(text: Optional[str]) -> str:
    """面積單位各寫法統一為 m2（㎡/平方公尺/平方米/M² … → m2）。"""
    if not text:
        return ""
    return _AREA_UNIT_RE.sub("m2", unicodedata.normalize("NFKC", text))


def parse_number(text: Optional[str]) -> Optional[float]:
    """從文字抽出數值：容忍千分位（半/全形逗號）、全形數字、內嵌空白。

    「2,812.5」「２，８１２」「2 812」→ 2812(.5)。抽不到回 None。
    """
    if text is None:
        return None
    norm = unicodedata.normalize("NFKC", str(text)).replace(" ", "").replace("　", "")
    m = _NUM_RE.search(norm)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "").replace("，", ""))
    except ValueError:
        return None


def roc_to_ad(year: int) -> int:
    """民國年 → 西元年。"""
    return year + _ROC_OFFSET


def normalize_date(text: Optional[str]) -> Optional[str]:
    """日期正規化為西元 YYYY-MM-DD。支援：
    - 民國：112年3月24日 / 112.03.24 / 112/3/24（年 < 1911 視為民國）
    - 西元：2023年3月24日 / 2023-03-24 / 2023/3/24
    抽不到回 None。
    """
    if not text:
        return None
    norm = unicodedata.normalize("NFKC", text)
    m = re.search(r"(\d{2,4})\s*[年./-]\s*(\d{1,2})\s*[月./-]\s*(\d{1,2})", norm)
    if not m:
        return None
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if year < _ROC_OFFSET:  # 民國年
        year = roc_to_ad(year)
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def values_equal(a: Optional[str], b: Optional[str], *, num_tol: float = 0.0) -> bool:
    """寬鬆相等：吸收全半形/千分位/臺台/單位/日期差異。

    判定順序：兩者皆空 → 相等；數值可解析 → 依 num_tol 比數值；日期可解析 → 比日期；
    否則比正規化文字（含面積單位統一）。用於「模型輸出 vs gold」評分與一致性檢核。
    """
    a_empty = a is None or str(a).strip() == ""
    b_empty = b is None or str(b).strip() == ""
    if a_empty and b_empty:
        return True
    if a_empty or b_empty:
        return False

    na, nb = parse_number(a), parse_number(b)
    if na is not None and nb is not None:
        # 僅在「整串就是一個數」時走數值比對，避免把地號等含數字文字誤判
        if _NUM_RE.fullmatch(unicodedata.normalize("NFKC", str(a)).replace(" ", "")
                             .replace("　", "")) and \
           _NUM_RE.fullmatch(unicodedata.normalize("NFKC", str(b)).replace(" ", "")
                             .replace("　", "")):
            return abs(na - nb) <= num_tol

    da, db = normalize_date(a), normalize_date(b)
    if da and db:
        return da == db

    return normalize_area_unit(normalize_text(a)) == normalize_area_unit(normalize_text(b))
