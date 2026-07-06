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
