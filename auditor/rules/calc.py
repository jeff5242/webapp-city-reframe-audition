from __future__ import annotations

from ..models import AuditData, Finding
from .engine import Rule

_BONUS_LIMIT_RATIO = 0.5   # 都更條例第65條：容積獎勵上限 = 法定容積 × 50%
_BONUS_LIMIT_TOLERANCE = 2.0  # 容許計算誤差（m²）


class ActualParkingRule(Rule):
    rule_id = "CALC-003"
    rule_name = "實設停車位不低於法定要求"
    severity = "critical"
    reference = "建築技術規則第59條"

    def evaluate(self, data: AuditData) -> Finding:
        rt = data.review_table
        if rt is None:
            return self._skip("審議資料表未能解析")

        if rt.actual_parking is None or rt.legal_parking is None:
            return self._warn("實設或法定停車位資料不足，建議人工確認")

        evidence = f"審議資料表第 {rt.raw_page} 頁"
        applied = f"實設汽車停車位 {rt.actual_parking} 輛"
        expected = f"法定汽車停車位 {rt.legal_parking} 輛（建築技術規則第59條）"

        if rt.actual_parking < rt.legal_parking:
            short = rt.legal_parking - rt.actual_parking
            return self._fail(
                f"實設停車位 {rt.actual_parking} 輛低於法定要求 {rt.legal_parking} 輛，"
                f"差距 {short} 輛",
                evidence=evidence,
                applied_value=applied,
                expected_calc=expected,
                computed_result=f"短少 {short} 輛（{rt.actual_parking} ＜ {rt.legal_parking}）",
            )

        return self._pass(
            f"實設 {rt.actual_parking} 輛 ≥ 法定 {rt.legal_parking} 輛",
            evidence=evidence,
            applied_value=applied,
            expected_calc=expected,
            computed_result=f"符合（{rt.actual_parking} ≥ {rt.legal_parking}）",
        )


class BonusLimitVerifyRule(Rule):
    rule_id = "CALC-004"
    rule_name = "容積獎勵上限計算驗算"
    severity = "high"
    reference = "都更條例第65條"

    def evaluate(self, data: AuditData) -> Finding:
        rt = data.review_table
        if rt is None:
            return self._skip("審議資料表未能解析")

        if rt.base_floor_area is None or rt.bonus_limit is None:
            return self._warn(
                "基準樓地板面積或容積獎勵上限資料不足，建議人工確認"
            )

        expected = rt.base_floor_area * _BONUS_LIMIT_RATIO
        diff = abs(rt.bonus_limit - expected)

        evidence = f"審議資料表第 {rt.raw_page} 頁"
        applied = f"容積獎勵上限（填報）{rt.bonus_limit:,.2f} m²"
        expected_txt = f"基準容積 {rt.base_floor_area:,.2f} × 50% = {expected:,.2f} m²（都更條例第65條）"

        if diff > _BONUS_LIMIT_TOLERANCE:
            return self._fail(
                f"容積獎勵上限 {rt.bonus_limit:,.2f}m² 與計算值 {expected:,.2f}m² "
                f"（基準 {rt.base_floor_area:,.2f}m² × 50%）差距 {diff:.2f}m²，超過允許誤差",
                evidence=evidence,
                applied_value=applied,
                expected_calc=expected_txt,
                computed_result=f"與計算值差距 {diff:,.2f} m²，超過允許誤差 {_BONUS_LIMIT_TOLERANCE} m²",
            )

        return self._pass(
            f"容積獎勵上限 {rt.bonus_limit:,.2f}m² 與計算值一致"
            f"（{rt.base_floor_area:,.2f}m² × 50% = {expected:,.2f}m²）",
            evidence=evidence,
            applied_value=applied,
            expected_calc=expected_txt,
            computed_result=f"與計算值一致（差 {diff:,.2f} m² ≤ 允許誤差 {_BONUS_LIMIT_TOLERANCE} m²）",
        )
