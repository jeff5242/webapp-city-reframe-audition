"""
Unit tests for rule engine.
Test data reflects the two real cases audited:
  - Case 1 (士林芝山段): bonus_floor_area EXCEEDS limit by ~51m²
  - Case 2 (合家歡東湖段): accessible_parking filled as 0 but should be 1
"""
from __future__ import annotations

import pytest
from auditor.models import AuditData, FrontDoc, FrontDocsData, PiiRisk, ReviewTableData
from auditor.rules.document import (
    AffidavitRule,
    ApplicationFormRule,
    PowerOfAttorneyRule,
    ReviewTablePresentRule,
)
from auditor.rules.form import (
    AccessibleParkingRule,
    BonusFloorAreaLimitRule,
    EvParkingFieldRule,
    FillDateRule,
    SubmissionTypeRule,
)
from auditor.rules.pii import HighRiskPiiRule
from auditor.rules.consistency import WrongTermRule, NumberConsistencyRule
from auditor.rules.calc import ActualParkingRule, BonusLimitVerifyRule
from auditor.models import WrongTermMatch, NumberContext


def _make_data(
    case_name="測試案",
    implementer="測試建設股份有限公司",
    submission_type="B-1",
    fill_date="113年9月2日",
    bonus_floor_area=1000.0,
    bonus_limit=1000.0,
    base_floor_area=2000.0,
    legal_parking=30,
    actual_parking=35,
    accessible_parking=1,
    ev_parking=0,
    has_application=True,
    has_affidavit=True,
    poa_count=1,
    has_review_table=True,
    pii_risks=(),
) -> AuditData:
    rt = ReviewTableData(
        case_name=case_name,
        implementer=implementer,
        implementer_id=None,
        submission_type=submission_type,
        fill_date=fill_date,
        land_area=None,
        base_floor_area=base_floor_area,
        bonus_floor_area=bonus_floor_area,
        bonus_limit=bonus_limit,
        legal_parking=legal_parking,
        actual_parking=actual_parking,
        accessible_parking=accessible_parking,
        ev_parking=ev_parking,
        owner_consent_ratio=None,
        raw_page=6,
    )
    fd = FrontDocsData(
        docs=(),
        poa_count=poa_count,
        has_application=has_application,
        has_affidavit=has_affidavit,
        has_review_table=has_review_table,
    )
    return AuditData(review_table=rt, front_docs=fd, pii_risks=pii_risks)


# ─── Document rules ───────────────────────────────────────────────────────────

class TestApplicationFormRule:
    def test_pass_when_present(self):
        data = _make_data(has_application=True)
        finding = ApplicationFormRule().evaluate(data)
        assert finding.status == "pass"

    def test_fail_when_absent(self):
        data = _make_data(has_application=False)
        finding = ApplicationFormRule().evaluate(data)
        assert finding.status == "fail"
        assert finding.severity == "critical"


class TestDocLocationEvidence:
    """副總回饋：申請書只標到目錄頁 → 目錄列出者須誠實標註，內容頁優先。"""

    def _data_with_docs(self, docs):
        fd = FrontDocsData(
            docs=tuple(docs), poa_count=0,
            has_application=any(d.doc_type == "申請書" for d in docs),
            has_affidavit=False,
            has_review_table=any(d.doc_type == "審議資料表" for d in docs),
        )
        return AuditData(review_table=None, front_docs=fd, pii_risks=[])

    def test_toc_only_application_flags_uncertain_page(self):
        data = self._data_with_docs([FrontDoc("申請書", page=2, from_toc=True)])
        f = ApplicationFormRule().evaluate(data)
        assert f.status == "pass"
        assert "目錄列出" in f.evidence
        assert "待人工核對" in f.message

    def test_content_page_preferred_over_toc(self):
        # 同時有目錄(第2頁)與內容頁(第25頁) → 顯示內容頁、不標「目錄列出」
        data = self._data_with_docs([
            FrontDoc("申請書", page=2, from_toc=True),
            FrontDoc("申請書", page=25, from_toc=False),
        ])
        f = ApplicationFormRule().evaluate(data)
        assert f.evidence == "第 25 頁"
        assert "目錄列出" not in f.message

    def test_content_page_plain_evidence(self):
        data = self._data_with_docs([FrontDoc("申請書", page=25, from_toc=False)])
        f = ApplicationFormRule().evaluate(data)
        assert f.evidence == "第 25 頁"

    def test_review_table_toc_only_flagged(self):
        data = self._data_with_docs([FrontDoc("審議資料表", page=2, from_toc=True)])
        f = ReviewTablePresentRule().evaluate(data)
        assert f.status == "pass"
        assert "目錄列出" in f.evidence


class TestAffidavitRule:
    def test_pass_when_present(self):
        finding = AffidavitRule().evaluate(_make_data(has_affidavit=True))
        assert finding.status == "pass"

    def test_fail_when_absent(self):
        finding = AffidavitRule().evaluate(_make_data(has_affidavit=False))
        assert finding.status == "fail"


class TestPowerOfAttorneyRule:
    def test_pass_when_at_least_one(self):
        finding = PowerOfAttorneyRule().evaluate(_make_data(poa_count=3))
        assert finding.status == "pass"

    def test_fail_when_none(self):
        finding = PowerOfAttorneyRule().evaluate(_make_data(poa_count=0))
        assert finding.status == "fail"


class TestReviewTablePresentRule:
    def test_pass_from_front_docs(self):
        finding = ReviewTablePresentRule().evaluate(_make_data(has_review_table=True))
        assert finding.status == "pass"

    def test_pass_from_review_table_extraction(self):
        data = _make_data(has_review_table=False)
        finding = ReviewTablePresentRule().evaluate(data)
        # review_table is present from _make_data, so it should still pass
        assert finding.status == "pass"


# ─── Form rules ───────────────────────────────────────────────────────────────

class TestSubmissionTypeRule:
    def test_pass_for_valid_type(self):
        for t in ["A-1", "B-1", "B-2", "C", "D"]:
            finding = SubmissionTypeRule().evaluate(_make_data(submission_type=t))
            assert finding.status == "pass", f"Expected pass for {t}"

    def test_warn_for_unknown(self):
        finding = SubmissionTypeRule().evaluate(_make_data(submission_type=None))
        assert finding.status == "warn"


class TestFillDateRule:
    def test_pass_when_date_present(self):
        finding = FillDateRule().evaluate(_make_data(fill_date="113年9月2日"))
        assert finding.status == "pass"

    def test_warn_when_absent(self):
        finding = FillDateRule().evaluate(_make_data(fill_date=None))
        assert finding.status == "warn"


class TestBonusFloorAreaLimitRule:
    def test_derives_limit_from_base_when_no_explicit_limit(self):
        # 表無明列上限，但有基準容積 → 上限=基準×50%，仍可核（大魯閣）
        f = BonusFloorAreaLimitRule().evaluate(
            _make_data(base_floor_area=2812.0, bonus_floor_area=1406.0, bonus_limit=None)
        )
        assert f.status == "pass"
        assert "推算" in (f.expected_calc or "")

    def test_derived_limit_can_fail(self):
        f = BonusFloorAreaLimitRule().evaluate(
            _make_data(base_floor_area=2812.0, bonus_floor_area=1500.0, bonus_limit=None)
        )
        assert f.status == "fail"

    def test_warn_when_no_limit_and_no_base(self):
        f = BonusFloorAreaLimitRule().evaluate(
            _make_data(base_floor_area=None, bonus_floor_area=1406.0, bonus_limit=None)
        )
        assert f.status == "warn"

    def test_pass_when_within_limit(self):
        finding = BonusFloorAreaLimitRule().evaluate(
            _make_data(bonus_floor_area=1877.63, bonus_limit=1877.63)
        )
        assert finding.status == "pass"

    def test_fail_case1_exceeds_limit(self):
        """Case 1 (士林芝山段): 1928.58 > 1877.63"""
        finding = BonusFloorAreaLimitRule().evaluate(
            _make_data(bonus_floor_area=1928.58, bonus_limit=1877.63)
        )
        assert finding.status == "fail"
        assert finding.severity == "critical"
        assert "1,928.58" in finding.message
        assert "1,877.63" in finding.message

    def test_warn_when_data_missing(self):
        rt = ReviewTableData(
            case_name=None, implementer=None, implementer_id=None,
            submission_type=None, fill_date=None, land_area=None,
            base_floor_area=None, bonus_floor_area=None, bonus_limit=None,
            legal_parking=None, actual_parking=None, accessible_parking=None,
            ev_parking=None, owner_consent_ratio=None, raw_page=6,
        )
        data = AuditData(review_table=rt, front_docs=None, pii_risks=())
        finding = BonusFloorAreaLimitRule().evaluate(data)
        assert finding.status == "warn"

    def test_ratio_is_bonus_over_base_not_over_limit(self):
        """容積獎勵比率 = 獎勵 ÷ 基準容積（理事長 item 4）。

        獎勵 1,020 / 基準 2,000 = 51.0%（非 獎勵/上限 1,000 = 102.0%）。
        """
        finding = BonusFloorAreaLimitRule().evaluate(
            _make_data(bonus_floor_area=1020.0, bonus_limit=1000.0, base_floor_area=2000.0)
        )
        assert finding.status == "fail"
        assert "51.0%" in finding.message
        assert "102.0%" not in finding.message

    def test_ratio_derives_base_from_limit_when_base_missing(self):
        """基準缺漏時，依 上限=基準×50% 推回 基準=上限×2。

        獎勵 1,020 / (上限 1,000 × 2) = 51.0%。
        """
        finding = BonusFloorAreaLimitRule().evaluate(
            _make_data(bonus_floor_area=1020.0, bonus_limit=1000.0, base_floor_area=None)
        )
        assert finding.status == "fail"
        assert "51.0%" in finding.message


class TestAccessibleParkingRule:
    def test_pass_when_accessible_meets_threshold(self):
        finding = AccessibleParkingRule().evaluate(
            _make_data(legal_parking=33, accessible_parking=1)
        )
        assert finding.status == "pass"

    def test_fail_case2_zero_accessible_with_small_lot(self):
        """Case 2 (合家歡): 33 legal spaces but 0 accessible"""
        finding = AccessibleParkingRule().evaluate(
            _make_data(legal_parking=33, accessible_parking=0)
        )
        assert finding.status == "fail"
        assert finding.severity == "critical"
        assert "33" in finding.message

    def test_fail_when_51_spaces_zero_accessible(self):
        """51 legal spaces needs 2 accessible per 建築技術規則"""
        finding = AccessibleParkingRule().evaluate(
            _make_data(legal_parking=51, accessible_parking=0)
        )
        assert finding.status == "fail"

    def test_pass_case1_58_legal_2_accessible(self):
        """Case 1: 58 legal → required 2 accessible → has 2 → pass"""
        finding = AccessibleParkingRule().evaluate(
            _make_data(legal_parking=58, accessible_parking=2)
        )
        assert finding.status == "pass"


class TestEvParkingFieldRule:
    def test_pass_when_field_present(self):
        finding = EvParkingFieldRule().evaluate(_make_data(ev_parking=0))
        assert finding.status == "pass"

    def test_fail_when_field_missing(self):
        finding = EvParkingFieldRule().evaluate(_make_data(ev_parking=None))
        assert finding.status == "fail"
        assert finding.severity == "high"


# ─── PII rules ────────────────────────────────────────────────────────────────

class TestHighRiskPiiRule:
    def test_pass_when_no_pii(self):
        finding = HighRiskPiiRule().evaluate(_make_data(pii_risks=()))
        assert finding.status == "pass"

    def test_fail_when_high_risk_pii(self):
        risk = PiiRisk(
            page=2,
            risk_type="residential_address",
            value="○○路○段○號（已遮蔽）",
            context="理事長 ○○○ ○○路○段○號（已遮蔽）",
            severity="HIGH",
        )
        finding = HighRiskPiiRule().evaluate(_make_data(pii_risks=(risk,)))
        assert finding.status == "fail"
        assert "高風險" in finding.message or "高" in finding.message

    def test_warn_when_medium_risk_only(self):
        risk = PiiRisk(
            page=1,
            risk_type="phone",
            value="(02)2521-1822",
            context="電話 (02)2521-1822",
            severity="MEDIUM",
        )
        finding = HighRiskPiiRule().evaluate(_make_data(pii_risks=(risk,)))
        assert finding.status == "warn"


# ─── Consistency rules ────────────────────────────────────────────────────────

class TestWrongTermRule:
    def test_pass_when_no_matches(self):
        data = _make_data()
        finding = WrongTermRule().evaluate(data)
        assert finding.status == "pass"

    def test_fail_with_wrong_terms(self):
        match = WrongTermMatch(
            page=3,
            wrong_term="協議合件",
            correct_term="協議合建",
            context="採協議合件方式辦理都市更新",
        )
        data = AuditData(
            review_table=_make_data().review_table,
            front_docs=_make_data().front_docs,
            pii_risks=(),
            term_matches=(match,),
        )
        finding = WrongTermRule().evaluate(data)
        assert finding.status == "fail"
        assert "協議合件" in finding.evidence
        assert "協議合建" in finding.evidence

    def test_evidence_includes_page_number(self):
        match = WrongTermMatch(page=7, wrong_term="更新單位", correct_term="更新單元", context="更新單位範圍")
        data = AuditData(
            review_table=_make_data().review_table,
            front_docs=_make_data().front_docs,
            pii_risks=(),
            term_matches=(match,),
        )
        finding = WrongTermRule().evaluate(data)
        assert "P.7" in finding.evidence


class TestNumberConsistencyRule:
    def test_pass_when_no_context(self):
        data = _make_data()
        finding = NumberConsistencyRule().evaluate(data)
        assert finding.status == "warn"

    def test_pass_when_numbers_match(self):
        ctx = NumberContext(page=15, field="accessible_parking", value=2.0, raw_text="無障礙2輛")
        data = AuditData(
            review_table=_make_data(accessible_parking=2).review_table,
            front_docs=_make_data().front_docs,
            pii_risks=(),
            number_contexts=(ctx,),
        )
        finding = NumberConsistencyRule().evaluate(data)
        assert finding.status == "pass"

    def test_fail_when_numbers_conflict(self):
        """Main text says accessible_parking=3 but 審議資料表 has 2."""
        ctx = NumberContext(page=20, field="accessible_parking", value=3.0, raw_text="無障礙3輛")
        data = AuditData(
            review_table=_make_data(accessible_parking=2).review_table,
            front_docs=_make_data().front_docs,
            pii_risks=(),
            number_contexts=(ctx,),
        )
        finding = NumberConsistencyRule().evaluate(data)
        assert finding.status == "fail"
        assert finding.severity == "high"
        assert "無障礙停車位" in finding.evidence

    def test_skip_when_no_review_table(self):
        data = AuditData(review_table=None, front_docs=None, pii_risks=())
        finding = NumberConsistencyRule().evaluate(data)
        assert finding.status == "skip"


# ─── Calc rules ───────────────────────────────────────────────────────────────

class TestActualParkingRule:
    def test_pass_when_actual_meets_legal(self):
        finding = ActualParkingRule().evaluate(_make_data(legal_parking=58, actual_parking=108))
        assert finding.status == "pass"

    def test_fail_when_actual_below_legal(self):
        finding = ActualParkingRule().evaluate(_make_data(legal_parking=58, actual_parking=50))
        assert finding.status == "fail"
        assert finding.severity == "critical"
        assert "58" in finding.message
        assert "50" in finding.message

    def test_warn_when_data_missing(self):
        finding = ActualParkingRule().evaluate(_make_data(actual_parking=None))
        assert finding.status == "warn"


class TestBonusLimitVerifyRule:
    def test_pass_when_limit_matches_calculation(self):
        # base=2000, bonus_limit=1000 = 2000 × 50%
        finding = BonusLimitVerifyRule().evaluate(
            _make_data(base_floor_area=2000.0, bonus_limit=1000.0)
        )
        assert finding.status == "pass"

    def test_fail_when_limit_is_wrong(self):
        # base=2000, stated limit=800 but expected 1000
        finding = BonusLimitVerifyRule().evaluate(
            _make_data(base_floor_area=2000.0, bonus_limit=800.0)
        )
        assert finding.status == "fail"
        assert finding.severity == "high"

    def test_pass_within_tolerance(self):
        # base=3755.26, limit=1877.63 (rounding in the form)
        finding = BonusLimitVerifyRule().evaluate(
            _make_data(base_floor_area=3755.26, bonus_limit=1877.63)
        )
        assert finding.status == "pass"

    def test_warn_when_data_missing(self):
        finding = BonusLimitVerifyRule().evaluate(_make_data(base_floor_area=None))
        assert finding.status == "warn"
