"""OCR agentic 驗證循環（constraint-driven self-correction）。

審議資料表欄位間有算術關係，可拿來當「驗證器」：若抽取出來的數字兜不攏公式，
就代表某格讀錯 → 重抽該格 → 再驗，疊代到收斂；仍無法滿足者升級人工。

這是「AI 辨識 → AI 用領域約束判斷比對 → 反覆疊代求最佳」的具體實作（Q1 所問）。
重抽函式 reextract 由外部注入（真實系統：高 zoom 重裁 / 換引擎 / 微調 VLM 重讀），
本模組只負責「驗證 + 決定重抽哪格 + 收斂判斷」，與抽取引擎解耦。

不使用 eval/exec；約束以具名函式白名單表達。
"""
from __future__ import annotations

from dataclasses import replace
from typing import Callable, List, Optional, Tuple

from ..models import ReviewTableData

# reextract(field_name, current_rt) -> 更可信的新值，或 None（無法改善）
Reextract = Callable[[str, ReviewTableData], Optional[float]]

_REL_TOL = 0.01


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(_REL_TOL, abs(b) * _REL_TOL)


class Constraint:
    """一條算術約束。check 回傳 True/False，資料不足回傳 None（跳過，不誤判）。"""

    def __init__(self, name: str, suspect: str,
                 check: Callable[[ReviewTableData], Optional[bool]]):
        self.name = name
        self.suspect = suspect  # 違反時最可疑（要重抽）的欄位
        self.check = check


def _c_bonus_limit(rt: ReviewTableData) -> Optional[bool]:
    if rt.base_floor_area is None or rt.bonus_limit is None:
        return None
    return _close(rt.bonus_limit, rt.base_floor_area * 0.5)


def _c_bonus_within(rt: ReviewTableData) -> Optional[bool]:
    if rt.bonus_floor_area is None or rt.bonus_limit is None:
        return None
    return rt.bonus_floor_area <= rt.bonus_limit * (1 + _REL_TOL)


# 約束白名單（可再擴充：停車位加總、獎勵比率 = 獎勵/基準 …）
CONSTRAINTS: List[Constraint] = [
    Constraint("衍生獎勵上限 = 基準容積 × 50%", "bonus_limit", _c_bonus_limit),
    Constraint("合計獎勵樓地板 ≤ 獎勵上限", "bonus_floor_area", _c_bonus_within),
]


def verify_review_table(
    rt: ReviewTableData,
    reextract: Optional[Reextract] = None,
    max_iters: int = 3,
) -> Tuple[ReviewTableData, List[str], List[str]]:
    """跑驗證循環。回傳 (修正後的 rt, 過程 log, 殘留無法滿足的約束名稱)。

    殘留非空 = 需升級人工複核（附上是哪條約束、哪格可疑）。
    """
    log: List[str] = []
    for it in range(1, max_iters + 1):
        failed = [c for c in CONSTRAINTS if c.check(rt) is False]
        if not failed:
            break
        progressed = False
        for c in failed:
            cur = getattr(rt, c.suspect)
            log.append(f"[疊代 {it}] 違反約束「{c.name}」→ 疑似「{c.suspect}」讀錯（現值 {cur}）")
            if reextract is not None:
                nv = reextract(c.suspect, rt)
                if nv is not None and nv != cur:
                    log.append(f"          重抽「{c.suspect}」：{cur} → {nv}")
                    rt = replace(rt, **{c.suspect: nv})
                    progressed = True
        if not progressed:
            log.append(f"[疊代 {it}] 無法再改善 → 停止，升級人工")
            break
    residual = [c.name for c in CONSTRAINTS if c.check(rt) is False]
    if not residual:
        log.append("✓ 所有算術約束滿足 → 抽取結果自洽")
    return rt, log, residual
