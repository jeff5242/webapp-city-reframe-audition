from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from ..models import AuditData, Finding


class Rule(ABC):
    rule_id: str
    rule_name: str
    severity: str
    reference: str = ""

    @abstractmethod
    def evaluate(self, data: AuditData) -> Finding:
        ...

    def _pass(
        self,
        message: str,
        evidence: str = "",
        applied_value: str = None,
        expected_calc: str = None,
        computed_result: str = None,
    ) -> Finding:
        return Finding(
            rule_id=self.rule_id,
            rule_name=self.rule_name,
            status="pass",
            severity=self.severity,
            message=message,
            evidence=evidence or None,
            reference=self.reference or None,
            applied_value=applied_value,
            expected_calc=expected_calc,
            computed_result=computed_result,
        )

    def _fail(
        self,
        message: str,
        evidence: str = "",
        applied_value: str = None,
        expected_calc: str = None,
        computed_result: str = None,
    ) -> Finding:
        return Finding(
            rule_id=self.rule_id,
            rule_name=self.rule_name,
            status="fail",
            severity=self.severity,
            message=message,
            evidence=evidence or None,
            reference=self.reference or None,
            applied_value=applied_value,
            expected_calc=expected_calc,
            computed_result=computed_result,
        )

    def _warn(self, message: str, evidence: str = "") -> Finding:
        return Finding(
            rule_id=self.rule_id,
            rule_name=self.rule_name,
            status="warn",
            severity=self.severity,
            message=message,
            evidence=evidence or None,
        )

    def _skip(self, message: str) -> Finding:
        return Finding(
            rule_id=self.rule_id,
            rule_name=self.rule_name,
            status="skip",
            severity=self.severity,
            message=message,
        )


class RuleEngine:
    def __init__(self, rules: List[Rule]):
        self.rules = rules

    def evaluate(self, data: AuditData) -> List[Finding]:
        return [rule.evaluate(data) for rule in self.rules]


def build_default_engine() -> RuleEngine:
    from .document import (
        ApplicationFormRule,
        AffidavitRule,
        PowerOfAttorneyRule,
        ReviewTablePresentRule,
    )
    from .form import (
        SubmissionTypeRule,
        FillDateRule,
        BonusFloorAreaLimitRule,
        AccessibleParkingRule,
        EvParkingFieldRule,
    )
    from .pii import HighRiskPiiRule
    from .consistency import WrongTermRule, NumberConsistencyRule
    from .calc import ActualParkingRule, BonusLimitVerifyRule

    return RuleEngine([
        ApplicationFormRule(),
        AffidavitRule(),
        PowerOfAttorneyRule(),
        ReviewTablePresentRule(),
        SubmissionTypeRule(),
        FillDateRule(),
        BonusFloorAreaLimitRule(),
        AccessibleParkingRule(),
        ActualParkingRule(),
        BonusLimitVerifyRule(),
        EvParkingFieldRule(),
        HighRiskPiiRule(),
        WrongTermRule(),
        NumberConsistencyRule(),
    ])


def build_engine_with_playbook(version: str = "111", playbook_path: str = None) -> RuleEngine:
    """14 條手寫規則 + playbook 宣告式規則。playbook 缺檔時等同 build_default_engine。

    對應提升方案「缺口 3：規則外部化」——新增規則改 playbook JSON 即可。
    """
    from .playbook import load_playbook, default_playbook_path

    engine = build_default_engine()
    path = playbook_path or default_playbook_path(version)
    if path:
        # 只納入 enabled 規則進主引擎（草稿/未複核者不進報告，避免 skip 雜訊與重複判定）
        live = [r for r in load_playbook(path) if r.spec.get("enabled", True)]
        engine.rules.extend(live)
    return engine
