from __future__ import annotations

import math

from ..models import AuditData, Finding
from .engine import Rule

_VALID_SUBMISSION_TYPES = {"A-1", "B-1", "B-2", "C", "D"}


def _required_accessible(legal: int) -> int:
    """建築技術規則第167條之六：計算應設無障礙停車位數量"""
    if legal <= 0:
        return 0
    if legal <= 50:
        return 1
    if legal <= 300:
        return 1 + math.ceil((legal - 50) / 50)
    return 6 + math.ceil((legal - 300) / 100)


class SubmissionTypeRule(Rule):
    rule_id = "FORM-001"
    rule_name = "送審類別已勾選"
    severity = "high"

    def evaluate(self, data: AuditData) -> Finding:
        rt = data.review_table
        if rt is None:
            return self._skip("審議資料表未能解析")
        if rt.submission_type in _VALID_SUBMISSION_TYPES:
            return self._pass(f"送審類別：{rt.submission_type}")
        return self._warn("送審類別未能從審議資料表識別，請人工確認")


class FillDateRule(Rule):
    rule_id = "FORM-002"
    rule_name = "審議資料表填表日期已填寫"
    severity = "medium"

    def evaluate(self, data: AuditData) -> Finding:
        rt = data.review_table
        if rt is None:
            return self._skip("審議資料表未能解析")
        if rt.fill_date:
            return self._pass(f"填表日期：{rt.fill_date}")
        return self._warn("填表日期未能識別，請人工確認")


class BonusFloorAreaLimitRule(Rule):
    rule_id = "CALC-001"
    rule_name = "容積獎勵申請額度不超過上限"
    severity = "critical"
    reference = "都更條例第65條；111年版修訂第6點"

    def evaluate(self, data: AuditData) -> Finding:
        rt = data.review_table
        if rt is None:
            return self._skip("審議資料表未能解析")

        if rt.bonus_floor_area is None or rt.bonus_limit is None:
            return self._warn(
                "容積獎勵申請額度或上限無法從審議資料表解析，請人工核對",
                evidence=f"審議資料表第 {rt.raw_page} 頁",
            )

        # 容積獎勵比率 = 獎勵樓地板 ÷ 基準容積（非 ÷ 上限）。
        # 基準缺漏時依「上限 = 基準 × 50%」推回 基準 = 上限 × 2。
        base = rt.base_floor_area or (rt.bonus_limit * 2)
        evidence = f"審議資料表第 {rt.raw_page} 頁"
        applied = f"獎勵樓地板面積 {rt.bonus_floor_area:,.2f} m²"
        expected = (
            f"基準容積 {base:,.2f} × 50% = 上限 {rt.bonus_limit:,.2f} m²"
            "（都更條例第65條）"
        )
        ratio = rt.bonus_floor_area / base * 100 if base > 0 else 0

        if rt.bonus_floor_area > rt.bonus_limit + 0.1:
            diff = rt.bonus_floor_area - rt.bonus_limit
            return self._fail(
                f"申請額 {rt.bonus_floor_area:,.2f}m² 超過上限 {rt.bonus_limit:,.2f}m²"
                f"，差距 {diff:,.2f}m²，獎勵比率 {ratio:.1f}%",
                evidence=evidence,
                applied_value=applied,
                expected_calc=expected,
                computed_result=f"超出上限 {diff:,.2f} m²，獎勵比率 {ratio:.1f}% ＞ 50%",
            )

        return self._pass(
            f"容積獎勵 {rt.bonus_floor_area:,.2f}m² ≤ 上限 {rt.bonus_limit:,.2f}m²",
            evidence=evidence,
            applied_value=applied,
            expected_calc=expected,
            computed_result=f"未超上限，獎勵比率 {ratio:.1f}% ≤ 50%",
        )


class AccessibleParkingRule(Rule):
    rule_id = "CALC-002"
    rule_name = "無障礙停車位數量符合法定最低要求"
    severity = "critical"
    reference = "建築技術規則第167條之六；111年版修訂第2點"

    def evaluate(self, data: AuditData) -> Finding:
        rt = data.review_table
        if rt is None:
            return self._skip("審議資料表未能解析")

        if rt.accessible_parking is None:
            return self._warn(
                "無障礙停車位欄位無法從審議資料表解析，請人工確認",
                evidence=f"審議資料表第 {rt.raw_page} 頁",
            )

        legal = rt.legal_parking
        accessible = rt.accessible_parking
        evidence = f"審議資料表第 {rt.raw_page} 頁"

        if legal is not None and legal > 0:
            required = _required_accessible(legal)
            applied = f"無障礙停車位 {accessible} 輛"
            expected = (
                f"法定停車 {legal} 輛 → 依建築技術規則§167-6 應設 {required} 輛"
            )
            if accessible < required:
                return self._fail(
                    f"法定(含無障礙)汽車停車位 {legal} 輛，依建築技術規則應設 {required} 輛無障礙停車位，"
                    f"但填寫為 {accessible} 輛",
                    evidence=evidence,
                    applied_value=applied,
                    expected_calc=expected,
                    computed_result=f"短少 {required - accessible} 輛（{accessible} ＜ {required}）",
                )
            return self._pass(
                f"無障礙停車位 {accessible} 輛 ≥ 法定要求 {required} 輛（法定停車 {legal} 輛）",
                applied_value=applied,
                expected_calc=expected,
                computed_result=f"符合（{accessible} ≥ {required}）",
                evidence=f"審議資料表第 {rt.raw_page} 頁",
            )

        return self._pass(
            f"無障礙停車位 {accessible} 輛"
            + (f"（法定 {legal} 輛）" if legal is not None else ""),
            evidence=f"審議資料表第 {rt.raw_page} 頁",
        )


class EvParkingFieldRule(Rule):
    rule_id = "FORM-003"
    rule_name = "充電車位欄位存在（111年版新增）"
    severity = "high"
    reference = "111年版修訂第2點"

    def evaluate(self, data: AuditData) -> Finding:
        rt = data.review_table
        if rt is None:
            return self._skip("審議資料表未能解析")

        if rt.ev_parking is not None:
            return self._pass(
                f"充電車位欄位已填寫：{rt.ev_parking} 輛",
                evidence=f"審議資料表第 {rt.raw_page} 頁",
            )

        return self._fail(
            "充電車位欄位未在審議資料表中找到，可能使用舊版格式",
            evidence=f"審議資料表第 {rt.raw_page} 頁" if rt.raw_page else "",
        )
