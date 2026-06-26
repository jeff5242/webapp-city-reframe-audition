"""Tests for field_auditor — pure validation functions + mocked LLM extraction."""
from __future__ import annotations

import types
from unittest.mock import MagicMock, patch

from auditor.parsing_pipeline.field_auditor import (
    BonusItem,
    ConsentRatio,
    ExtractedFields,
    FieldFinding,
    _BONUS_CAP_PCT,
    _CONSENT_AREA_MIN_PCT,
    _CONSENT_OWNER_MIN_PCT,
    _parse_extracted,
    validate_fields,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fields(**kw) -> ExtractedFields:
    defaults = dict(
        bonus_items=[],
        bonus_total_pct=None,
        consent_ratio=None,
        application_date=None,
        affidavit_date=None,
        power_of_attorney_date=None,
        implementer_name=None,
        land_area_sqm=None,
    )
    return ExtractedFields(**{**defaults, **kw})


def _consent(owner_pct=None, area_pct=None, page=1) -> ConsentRatio:
    return ConsentRatio(owner_count_pct=owner_pct, land_area_pct=area_pct, source_page=page)


# ── validate_fields — 容積獎勵 ─────────────────────────────────────────────────

class TestBonusCapValidation:
    def test_total_within_cap_no_finding(self):
        f = _fields(bonus_total_pct=40.0)
        assert validate_fields(f) == []

    def test_total_exactly_at_cap_no_finding(self):
        f = _fields(bonus_total_pct=_BONUS_CAP_PCT)
        assert validate_fields(f) == []

    def test_total_exceeds_cap_critical(self):
        f = _fields(bonus_total_pct=41.0)
        findings = validate_fields(f)
        assert len(findings) == 1
        assert findings[0].rule_id == "LAW-FAR-001"
        assert findings[0].severity == "critical"
        assert "41.0%" in findings[0].actual_value

    def test_computed_sum_exceeds_cap_critical(self):
        items = [
            BonusItem("項目A", 25.0, "第65條第1款"),
            BonusItem("項目B", 20.0, "第65條第2款"),
        ]
        f = _fields(bonus_items=items)   # no bonus_total_pct stated
        findings = validate_fields(f)
        assert any(f.rule_id == "LAW-FAR-001" for f in findings)
        assert "45.0%" in findings[0].actual_value

    def test_computed_sum_within_cap_no_finding(self):
        items = [
            BonusItem("項目A", 15.0, "第65條第1款"),
            BonusItem("項目B", 10.0, "第65條第2款"),
        ]
        f = _fields(bonus_items=items)
        assert validate_fields(f) == []

    def test_total_stated_takes_precedence_over_items(self):
        # bonus_total_pct is within cap even though items sum to 50%
        items = [BonusItem("X", 25.0, ""), BonusItem("Y", 25.0, "")]
        f = _fields(bonus_items=items, bonus_total_pct=35.0)
        assert validate_fields(f) == []

    def test_no_bonus_data_no_finding(self):
        assert validate_fields(_fields()) == []


# ── validate_fields — 同意比率 ────────────────────────────────────────────────

class TestConsentRatioValidation:
    def test_both_ratios_meet_threshold_no_finding(self):
        f = _fields(consent_ratio=_consent(owner_pct=70.0, area_pct=80.0))
        assert validate_fields(f) == []

    def test_owner_count_below_threshold_critical(self):
        f = _fields(consent_ratio=_consent(owner_pct=60.0, area_pct=80.0))
        findings = validate_fields(f)
        assert any(ff.rule_id == "LAW-CON-001" for ff in findings)
        assert findings[0].severity == "critical"

    def test_owner_count_exactly_at_threshold_no_finding(self):
        f = _fields(consent_ratio=_consent(owner_pct=_CONSENT_OWNER_MIN_PCT, area_pct=80.0))
        assert not any(ff.rule_id == "LAW-CON-001" for ff in validate_fields(f))

    def test_land_area_below_threshold_critical(self):
        f = _fields(consent_ratio=_consent(owner_pct=70.0, area_pct=70.0))
        findings = validate_fields(f)
        assert any(ff.rule_id == "LAW-CON-002" for ff in findings)
        assert findings[0].severity == "critical"

    def test_land_area_exactly_at_threshold_no_finding(self):
        f = _fields(consent_ratio=_consent(owner_pct=70.0, area_pct=_CONSENT_AREA_MIN_PCT))
        assert not any(ff.rule_id == "LAW-CON-002" for ff in validate_fields(f))

    def test_both_below_threshold_two_findings(self):
        f = _fields(consent_ratio=_consent(owner_pct=50.0, area_pct=60.0))
        rule_ids = {ff.rule_id for ff in validate_fields(f)}
        assert "LAW-CON-001" in rule_ids
        assert "LAW-CON-002" in rule_ids

    def test_none_ratios_skipped(self):
        f = _fields(consent_ratio=_consent(owner_pct=None, area_pct=None))
        assert validate_fields(f) == []

    def test_no_consent_ratio_no_finding(self):
        assert validate_fields(_fields()) == []

    def test_finding_preserves_source_page(self):
        f = _fields(consent_ratio=_consent(owner_pct=50.0, page=42))
        findings = validate_fields(f)
        assert any(ff.page_number == 42 for ff in findings)


# ── validate_fields — 報核日期一致性 ─────────────────────────────────────────

class TestDateConsistency:
    def test_all_dates_equal_no_finding(self):
        f = _fields(
            application_date="2025-05-06",
            affidavit_date="2025-05-06",
            power_of_attorney_date="2025-05-06",
        )
        assert validate_fields(f) == []

    def test_two_dates_differ_warning(self):
        f = _fields(
            application_date="2025-05-06",
            affidavit_date="2025-04-01",
            power_of_attorney_date="2025-05-06",
        )
        findings = validate_fields(f)
        assert any(ff.rule_id == "CONS-DATE-001" for ff in findings)
        assert findings[0].severity == "warning"

    def test_all_three_dates_differ_one_finding(self):
        f = _fields(
            application_date="2025-01-01",
            affidavit_date="2025-02-01",
            power_of_attorney_date="2025-03-01",
        )
        date_findings = [ff for ff in validate_fields(f) if ff.rule_id == "CONS-DATE-001"]
        assert len(date_findings) == 1  # one finding summarises all mismatches

    def test_only_one_date_present_no_finding(self):
        f = _fields(application_date="2025-05-06")
        assert not any(ff.rule_id == "CONS-DATE-001" for ff in validate_fields(f))

    def test_all_dates_none_no_finding(self):
        assert validate_fields(_fields()) == []


# ── validate_fields — 法規版本切換 ────────────────────────────────────────────

class TestRegYearThresholds:
    """Verify that consent thresholds differ correctly across regulation years."""

    def test_111_year_owner_threshold(self):
        # 2/3 ≈ 66.67%; value just below threshold → finding
        f = _fields(consent_ratio=_consent(owner_pct=66.0, area_pct=80.0))
        findings = validate_fields(f, reg_year="111")
        assert any(ff.rule_id == "LAW-CON-001" for ff in findings)

    def test_111_year_owner_passes_at_threshold(self):
        from auditor.parsing_pipeline.field_auditor import _CONSENT_THRESHOLDS
        owner_min, _ = _CONSENT_THRESHOLDS["111"]
        f = _fields(consent_ratio=_consent(owner_pct=owner_min, area_pct=80.0))
        assert not any(ff.rule_id == "LAW-CON-001" for ff in validate_fields(f, reg_year="111"))

    def test_113_year_owner_threshold_higher(self):
        # 113年門檻 80%；70% passes for 111 but fails for 113
        f = _fields(consent_ratio=_consent(owner_pct=70.0, area_pct=85.0))
        assert validate_fields(f, reg_year="111") == []
        findings_113 = validate_fields(f, reg_year="113")
        assert any(ff.rule_id == "LAW-CON-001" for ff in findings_113)

    def test_113_year_area_threshold_higher(self):
        # 113年面積門檻 80%；75% passes for 111 but fails for 113
        f = _fields(consent_ratio=_consent(owner_pct=85.0, area_pct=75.0))
        assert validate_fields(f, reg_year="111") == []
        findings_113 = validate_fields(f, reg_year="113")
        assert any(ff.rule_id == "LAW-CON-002" for ff in findings_113)

    def test_113_year_both_at_80_no_finding(self):
        f = _fields(consent_ratio=_consent(owner_pct=80.0, area_pct=80.0))
        assert validate_fields(f, reg_year="113") == []

    def test_107_same_as_111(self):
        # 107/108 thresholds match 111
        f = _fields(consent_ratio=_consent(owner_pct=66.0, area_pct=80.0))
        assert validate_fields(f, reg_year="107") != []
        assert validate_fields(f, reg_year="108") != []

    def test_unknown_year_falls_back_to_111(self):
        f = _fields(consent_ratio=_consent(owner_pct=66.0, area_pct=80.0))
        findings_unknown = validate_fields(f, reg_year="999")
        findings_111 = validate_fields(f, reg_year="111")
        assert [ff.rule_id for ff in findings_unknown] == [ff.rule_id for ff in findings_111]

    def test_finding_message_contains_correct_threshold(self):
        f = _fields(consent_ratio=_consent(owner_pct=70.0, area_pct=85.0))
        findings = validate_fields(f, reg_year="113")
        owner_finding = next(ff for ff in findings if ff.rule_id == "LAW-CON-001")
        assert "80.00%" in owner_finding.expected


# ── _parse_extracted ──────────────────────────────────────────────────────────

class TestParseExtracted:
    def _raw(self, **kw):
        base = {
            "bonus_items": [],
            "bonus_total_pct": None,
            "consent_ratio": None,
            "dates": {},
            "implementer_name": None,
            "land_area_sqm": None,
        }
        base.update(kw)
        return base

    def test_empty_raw_returns_all_none(self):
        result = _parse_extracted({})
        assert result.bonus_items == []
        assert result.bonus_total_pct is None
        assert result.consent_ratio is None

    def test_bonus_items_parsed(self):
        raw = self._raw(bonus_items=[
            {"name": "綠建築", "rate_pct": 10.0, "legal_basis": "第65條"},
        ])
        result = _parse_extracted(raw)
        assert len(result.bonus_items) == 1
        assert result.bonus_items[0].name == "綠建築"
        assert result.bonus_items[0].rate_pct == 10.0

    def test_malformed_bonus_item_skipped(self):
        raw = self._raw(bonus_items=[
            {"name": "有效項目", "rate_pct": 5.0, "legal_basis": ""},
            {"name": "缺少rate_pct"},   # malformed
        ])
        result = _parse_extracted(raw)
        assert len(result.bonus_items) == 1

    def test_consent_ratio_parsed(self):
        raw = self._raw(consent_ratio={
            "owner_count_pct": 75.0,
            "land_area_pct": 80.0,
            "source_page": 12,
        })
        result = _parse_extracted(raw)
        assert result.consent_ratio is not None
        assert result.consent_ratio.owner_count_pct == 75.0
        assert result.consent_ratio.source_page == 12

    def test_dates_parsed(self):
        raw = self._raw(dates={
            "application_date": "2025-05-06",
            "affidavit_date": "2025-05-06",
            "power_of_attorney_date": None,
        })
        result = _parse_extracted(raw)
        assert result.application_date == "2025-05-06"
        assert result.power_of_attorney_date is None

    def test_land_area_parsed(self):
        raw = self._raw(land_area_sqm=1234.56)
        result = _parse_extracted(raw)
        assert result.land_area_sqm == 1234.56

    def test_implementer_name_stripped(self):
        raw = self._raw(implementer_name="  測試建設股份有限公司  ")
        result = _parse_extracted(raw)
        assert result.implementer_name == "測試建設股份有限公司"

    def test_empty_implementer_name_becomes_none(self):
        raw = self._raw(implementer_name="   ")
        result = _parse_extracted(raw)
        assert result.implementer_name is None


# ── FieldFinding ──────────────────────────────────────────────────────────────

class TestFieldFinding:
    def _make(self) -> FieldFinding:
        return FieldFinding(
            field_name="容積獎勵合計",
            rule_id="LAW-FAR-001",
            severity="critical",
            actual_value="45.0%",
            expected="≤ 40%",
            reason="超限",
            page_number=5,
        )

    def test_as_dict_has_all_keys(self):
        d = self._make().as_dict()
        for key in ("field_name", "rule_id", "severity", "actual_value", "expected", "reason", "page_number"):
            assert key in d

    def test_as_dict_values(self):
        d = self._make().as_dict()
        assert d["rule_id"] == "LAW-FAR-001"
        assert d["page_number"] == 5


# ── extract_and_validate (LLM mocked) ────────────────────────────────────────

class TestExtractAndValidate:
    def _make_anthropic_mock(self, tool_input: dict):
        block = MagicMock()
        block.type = "tool_use"
        block.name = "extract_fields"
        block.input = tool_input
        response = MagicMock()
        response.content = [block]
        client = MagicMock()
        client.messages.create.return_value = response
        mod = types.ModuleType("anthropic")
        mod.Anthropic = MagicMock(return_value=client)
        return mod

    def test_raises_without_anthropic(self):
        import pytest
        with patch.dict("sys.modules", {"anthropic": None}):
            from importlib import reload
            import auditor.parsing_pipeline.field_auditor as m
            reload(m)
            with pytest.raises((ImportError, TypeError)):
                m.extract_and_validate("some markdown")

    def test_returns_extracted_and_findings(self):
        tool_input = {
            "bonus_items": [{"name": "綠建築", "rate_pct": 50.0, "legal_basis": "第65條"}],
            "bonus_total_pct": 50.0,   # exceeds 40% cap → critical finding
            "consent_ratio": {"owner_count_pct": 75.0, "land_area_pct": 80.0, "source_page": 3},
            "dates": {"application_date": "2025-05-06", "affidavit_date": "2025-05-06",
                      "power_of_attorney_date": "2025-05-06"},
            "implementer_name": "測試建設股份有限公司",
            "land_area_sqm": 500.0,
        }
        anthropic_mod = self._make_anthropic_mock(tool_input)

        with patch.dict("sys.modules", {"anthropic": anthropic_mod}):
            from importlib import reload
            import auditor.parsing_pipeline.field_auditor as m
            reload(m)
            extracted, findings = m.extract_and_validate("dummy markdown")

        assert extracted.bonus_total_pct == 50.0
        assert any(f.rule_id == "LAW-FAR-001" for f in findings)

    def test_no_violations_returns_empty_findings(self):
        tool_input = {
            "bonus_items": [],
            "bonus_total_pct": 30.0,
            "consent_ratio": {"owner_count_pct": 70.0, "land_area_pct": 80.0, "source_page": 1},
            "dates": {"application_date": "2025-05-06", "affidavit_date": "2025-05-06",
                      "power_of_attorney_date": "2025-05-06"},
            "implementer_name": "測試建設股份有限公司",
            "land_area_sqm": 500.0,
        }
        anthropic_mod = self._make_anthropic_mock(tool_input)

        with patch.dict("sys.modules", {"anthropic": anthropic_mod}):
            from importlib import reload
            import auditor.parsing_pipeline.field_auditor as m
            reload(m)
            _, findings = m.extract_and_validate("dummy markdown")

        assert findings == []
