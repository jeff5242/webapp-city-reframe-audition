"""Tests for cross_doc_comparator — all LLM calls are mocked."""
from __future__ import annotations

import types
from unittest.mock import MagicMock, patch

from auditor.parsing_pipeline.cross_doc_comparator import (
    CrossDocFinding,
    SharedFields,
    _compare,
    _opt_float,
    _opt_int,
    _opt_str,
)


# ── _compare unit tests (pure, no LLM) ───────────────────────────────────────


def _bp(**kw) -> SharedFields:
    defaults = dict(doc_label="事業計畫書", land_area_sqm=None, owner_count=None,
                    pre_renewal_value_wan=None, post_renewal_value_wan=None,
                    implementer_name=None)
    return SharedFields(**{**defaults, **kw})


def _re(**kw) -> SharedFields:
    defaults = dict(doc_label="權利變換計畫書", land_area_sqm=None, owner_count=None,
                    pre_renewal_value_wan=None, post_renewal_value_wan=None,
                    implementer_name=None)
    return SharedFields(**{**defaults, **kw})


class TestCompare:
    def test_identical_fields_no_findings(self):
        bp = _bp(land_area_sqm=1234.56, owner_count=10,
                 pre_renewal_value_wan=5000.0, post_renewal_value_wan=8000.0,
                 implementer_name="新潤建設股份有限公司")
        re = _re(land_area_sqm=1234.56, owner_count=10,
                 pre_renewal_value_wan=5000.0, post_renewal_value_wan=8000.0,
                 implementer_name="新潤建設股份有限公司")
        assert _compare(bp, re) == []

    def test_land_area_difference_critical(self):
        bp = _bp(land_area_sqm=1000.0)
        re = _re(land_area_sqm=1001.5)   # diff = 1.5 m² > 0.5 tolerance
        findings = _compare(bp, re)
        assert len(findings) == 1
        assert findings[0].rule_id == "CONS-AREA-001"
        assert findings[0].severity == "critical"

    def test_land_area_within_tolerance_no_finding(self):
        bp = _bp(land_area_sqm=1000.0)
        re = _re(land_area_sqm=1000.3)   # diff = 0.3 m² ≤ 0.5 tolerance
        assert _compare(bp, re) == []

    def test_owner_count_mismatch_critical(self):
        bp = _bp(owner_count=10)
        re = _re(owner_count=11)
        findings = _compare(bp, re)
        assert any(f.rule_id == "CONS-OWN-001" for f in findings)
        assert findings[0].severity == "critical"

    def test_owner_count_match_no_finding(self):
        bp = _bp(owner_count=10)
        re = _re(owner_count=10)
        assert _compare(bp, re) == []

    def test_pre_renewal_value_difference_warning(self):
        bp = _bp(pre_renewal_value_wan=5000.0)
        re = _re(pre_renewal_value_wan=5002.0)   # diff = 2 萬元 > 1 tolerance
        findings = _compare(bp, re)
        assert any(f.rule_id == "CONS-VAL-001" for f in findings)
        assert findings[0].severity == "warning"

    def test_pre_renewal_value_within_tolerance(self):
        bp = _bp(pre_renewal_value_wan=5000.0)
        re = _re(pre_renewal_value_wan=5000.5)   # diff = 0.5 ≤ 1.0 tolerance
        assert _compare(bp, re) == []

    def test_post_renewal_value_difference_warning(self):
        bp = _bp(post_renewal_value_wan=8000.0)
        re = _re(post_renewal_value_wan=8005.0)
        findings = _compare(bp, re)
        assert any(f.rule_id == "CONS-VAL-002" for f in findings)

    def test_implementer_name_mismatch_critical(self):
        bp = _bp(implementer_name="新潤建設股份有限公司")
        re = _re(implementer_name="新潤建設有限公司")   # 不同
        findings = _compare(bp, re)
        assert any(f.rule_id == "CONS-IMP-001" for f in findings)
        assert findings[0].severity == "critical"

    def test_implementer_name_match_no_finding(self):
        bp = _bp(implementer_name="新潤建設股份有限公司")
        re = _re(implementer_name="新潤建設股份有限公司")
        assert _compare(bp, re) == []

    def test_none_fields_skipped(self):
        # Both None → no finding (can't compare)
        bp = _bp(land_area_sqm=None)
        re = _re(land_area_sqm=None)
        assert _compare(bp, re) == []

    def test_one_side_none_skipped(self):
        # Only one side has the value → can't compare, no finding
        bp = _bp(land_area_sqm=1000.0)
        re = _re(land_area_sqm=None)
        assert _compare(bp, re) == []

    def test_multiple_findings(self):
        bp = _bp(land_area_sqm=1000.0, owner_count=10)
        re = _re(land_area_sqm=1002.0, owner_count=12)
        findings = _compare(bp, re)
        assert len(findings) == 2
        rule_ids = {f.rule_id for f in findings}
        assert "CONS-AREA-001" in rule_ids
        assert "CONS-OWN-001" in rule_ids

    def test_cross_doc_finding_frozen(self):
        import pytest
        f = CrossDocFinding(
            field_name="x", rule_id="y", severity="critical",
            business_plan_value="a", rights_exchange_value="b", reason="c",
        )
        with pytest.raises((AttributeError, TypeError)):
            f.field_name = "changed"  # type: ignore

    def test_cross_doc_finding_as_dict(self):
        f = CrossDocFinding(
            field_name="更新單元總面積", rule_id="CONS-AREA-001", severity="critical",
            business_plan_value="1000.00 m²", rights_exchange_value="1002.00 m²",
            reason="差異過大",
        )
        d = f.as_dict()
        assert d["rule_id"] == "CONS-AREA-001"
        assert d["severity"] == "critical"
        assert "business_plan_value" in d


# ── compare_documents integration (LLM mocked) ───────────────────────────────


def _make_anthropic_mock(bp_raw: dict, re_raw: dict):
    """Return a mock anthropic module whose Anthropic() client returns preset tool_use."""
    call_count = [0]

    def fake_create(**kwargs):
        block = MagicMock()
        block.type = "tool_use"
        block.name = "extract_shared_fields"
        block.input = bp_raw if call_count[0] == 0 else re_raw
        call_count[0] += 1
        resp = MagicMock()
        resp.content = [block]
        return resp

    client = MagicMock()
    client.messages.create.side_effect = fake_create
    mod = types.ModuleType("anthropic")
    mod.Anthropic = MagicMock(return_value=client)
    return mod


class TestCompareDocuments:
    def test_raises_without_anthropic(self):
        import pytest
        with patch.dict("sys.modules", {"anthropic": None}):
            from importlib import reload
            import auditor.parsing_pipeline.cross_doc_comparator as m
            reload(m)
            with pytest.raises((ImportError, TypeError)):
                m.compare_documents("md1", "md2")

    def test_area_discrepancy_detected(self):
        bp_raw = {"land_area_sqm": 1000.0, "owner_count": None,
                  "pre_renewal_value_wan": None, "post_renewal_value_wan": None,
                  "implementer_name": None}
        re_raw = {"land_area_sqm": 1002.0, "owner_count": None,
                  "pre_renewal_value_wan": None, "post_renewal_value_wan": None,
                  "implementer_name": None}
        anthropic_mod = _make_anthropic_mock(bp_raw, re_raw)

        with patch.dict("sys.modules", {"anthropic": anthropic_mod}):
            from importlib import reload
            import auditor.parsing_pipeline.cross_doc_comparator as m
            reload(m)
            findings = m.compare_documents("bp markdown", "re markdown")

        assert any(f.rule_id == "CONS-AREA-001" for f in findings)

    def test_no_findings_when_consistent(self):
        shared = {"land_area_sqm": 500.0, "owner_count": 5,
                  "pre_renewal_value_wan": 2000.0, "post_renewal_value_wan": 3000.0,
                  "implementer_name": "測試建設股份有限公司"}
        anthropic_mod = _make_anthropic_mock(shared, shared)

        with patch.dict("sys.modules", {"anthropic": anthropic_mod}):
            from importlib import reload
            import auditor.parsing_pipeline.cross_doc_comparator as m
            reload(m)
            findings = m.compare_documents("bp markdown", "re markdown")

        assert findings == []

    def test_llm_error_returns_no_findings(self):
        """If LLM call raises, extraction returns None fields → no findings."""
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("API error")
        mod = types.ModuleType("anthropic")
        mod.Anthropic = MagicMock(return_value=client)

        with patch.dict("sys.modules", {"anthropic": mod}):
            from importlib import reload
            import auditor.parsing_pipeline.cross_doc_comparator as m
            reload(m)
            findings = m.compare_documents("bp", "re")

        assert findings == []


# ── Helper function tests ─────────────────────────────────────────────────────


class TestHelpers:
    def test_opt_float_none(self):
        assert _opt_float(None) is None

    def test_opt_float_valid(self):
        assert _opt_float("1234.5") == 1234.5
        assert _opt_float(100) == 100.0

    def test_opt_float_invalid(self):
        assert _opt_float("abc") is None

    def test_opt_int_none(self):
        assert _opt_int(None) is None

    def test_opt_int_valid(self):
        assert _opt_int("10") == 10
        assert _opt_int(7.9) == 7

    def test_opt_int_invalid(self):
        assert _opt_int("abc") is None

    def test_opt_str_none(self):
        assert _opt_str(None) is None
        assert _opt_str("  ") is None

    def test_opt_str_valid(self):
        assert _opt_str("  測試  ") == "測試"
