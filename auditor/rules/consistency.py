from __future__ import annotations

from ..models import AuditData, Finding
from .engine import Rule


class WrongTermRule(Rule):
    rule_id = "TERM-001"
    rule_name = "法定用詞錯誤"
    severity = "medium"
    reference = "臺北市都市更新自治條例"

    def evaluate(self, data: AuditData) -> Finding:
        if not data.term_matches:
            return self._pass("未偵測到法定用詞錯誤")

        items = "; ".join(
            f"P.{m.page}「{m.wrong_term}」應為「{m.correct_term}」"
            for m in data.term_matches
        )
        return self._fail(
            f"發現 {len(data.term_matches)} 處用詞錯誤，請核對後修正",
            evidence=items,
        )


class NumberConsistencyRule(Rule):
    rule_id = "CONS-001"
    rule_name = "關鍵數字前後一致性"
    severity = "high"
    reference = ""

    _LABEL = {
        "accessible_parking": "無障礙停車位",
        "legal_parking": "法定(含無障礙)汽車停車位",
        "bonus_floor_area": "容積獎勵面積",
        "ev_parking": "充電車位",
    }

    def evaluate(self, data: AuditData) -> Finding:
        rt = data.review_table
        if rt is None:
            return self._skip("審議資料表未能解析，無法執行一致性檢查")

        if not data.number_contexts:
            return self._warn(
                "未能從主文取得數字資料，建議人工核對審議資料表與各章節數字是否一致"
            )

        _table_values: dict = {
            "accessible_parking": rt.accessible_parking,
            "legal_parking": rt.legal_parking,
            "bonus_floor_area": rt.bonus_floor_area,
            "ev_parking": rt.ev_parking,
        }

        inconsistencies: list = []
        for ctx in data.number_contexts:
            expected = _table_values.get(ctx.field)
            if expected is None:
                continue
            if abs(ctx.value - expected) > 0.1:
                label = self._LABEL.get(ctx.field, ctx.field)
                inconsistencies.append(
                    f"{label}：審議資料表={expected}，P.{ctx.page}={ctx.value}"
                )

        if inconsistencies:
            return self._fail(
                f"發現 {len(inconsistencies)} 處數字不一致，請核對後修正",
                evidence="; ".join(inconsistencies),
            )

        return self._pass("審議資料表關鍵數字與主文一致")
