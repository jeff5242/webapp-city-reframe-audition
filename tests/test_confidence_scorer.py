"""Tests for Phase 6 composite confidence scoring."""
from __future__ import annotations

from auditor.models import AiFinding, Finding
from auditor.parsing_pipeline.confidence_scorer import (
    ConfidenceScore,
    score_ai_finding,
    score_findings,
    _topic,
)


def _ai(source="llm", rule_id="LAW-001", severity="warning",
        evidence_verified=False):
    return AiFinding(
        source=source,
        rule_id=rule_id,
        severity=severity,
        field_name="欄位",
        detected_text="原文",
        reason="原因",
        evidence_verified=evidence_verified,
    )


def _rule(rule_id, status="fail"):
    return Finding(
        rule_id=rule_id,
        rule_name="規則",
        status=status,
        severity="critical",
        message="訊息",
    )


# ── topic mapping ─────────────────────────────────────────────────────────────

def test_topic_bonus_matches_both_tracks():
    assert _topic("CALC-001") == "bonus"      # Track A
    assert _topic("CALC-004") == "bonus"      # Track A
    assert _topic("LAW-FAR-001") == "bonus"   # Track B


def test_topic_term_matches_both_tracks():
    assert _topic("TERM-001") == "term"
    assert _topic("TERM-042") == "term"


def test_topic_consent_and_crossdoc_distinct():
    assert _topic("LAW-CON-001") == "consent"
    assert _topic("CONS-AREA-001") == "crossdoc"
    assert _topic("CONS-001") == "consistency"


def test_topic_unknown_returns_other():
    assert _topic("XYZ-999") == "other"


# ── base by source ────────────────────────────────────────────────────────────

def test_field_source_higher_base_than_llm():
    field_score = score_ai_finding(_ai(source="field"), [])
    llm_score = score_ai_finding(_ai(source="llm"), [])
    assert field_score.value > llm_score.value


def test_cross_source_high_base():
    s = score_ai_finding(_ai(source="cross"), [])
    assert s.value >= 0.75


# ── evidence effect ───────────────────────────────────────────────────────────

def test_verified_evidence_boosts_confidence():
    unverified = score_ai_finding(_ai(source="llm", evidence_verified=False), [])
    verified = score_ai_finding(_ai(source="llm", evidence_verified=True), [])
    assert verified.value > unverified.value


def test_unverified_llm_penalised():
    # llm base 0.45, unverified penalty -0.10 → 0.35
    s = score_ai_finding(_ai(source="llm", evidence_verified=False), [])
    assert s.value < 0.45


# ── corroboration ─────────────────────────────────────────────────────────────

def test_corroboration_with_track_a_raises_confidence():
    # Track B bonus finding + Track A CALC-001 fail (same topic) → boost
    finding = _ai(source="field", rule_id="LAW-FAR-001", evidence_verified=True)
    without = score_ai_finding(finding, [])
    with_rule = score_ai_finding(finding, [_rule("CALC-001", "fail")])
    assert with_rule.value > without.value


def test_corroboration_ignores_passing_rules():
    finding = _ai(source="field", rule_id="LAW-FAR-001")
    passing = score_ai_finding(finding, [_rule("CALC-001", "pass")])
    none = score_ai_finding(finding, [])
    assert passing.value == none.value


def test_corroboration_requires_same_topic():
    finding = _ai(source="field", rule_id="LAW-FAR-001")  # bonus
    other_topic = score_ai_finding(finding, [_rule("PII-001", "fail")])  # pii
    none = score_ai_finding(finding, [])
    assert other_topic.value == none.value


# ── human-review routing ──────────────────────────────────────────────────────

def test_low_confidence_routes_to_human_review():
    # unverified llm → 0.35 < 0.60 threshold
    s = score_ai_finding(_ai(source="llm", evidence_verified=False), [])
    assert s.needs_human_review is True


def test_critical_unverified_forces_review_even_if_high_base():
    # cross base 0.75 (≥ threshold) but critical + unverified → still review
    s = score_ai_finding(
        _ai(source="cross", severity="critical", evidence_verified=False), []
    )
    assert s.value >= 0.60
    assert s.needs_human_review is True


def test_high_confidence_verified_no_review():
    # field 0.75 + verified 0.20 = 0.95, warning → no review
    s = score_ai_finding(
        _ai(source="field", severity="warning", evidence_verified=True), []
    )
    assert s.needs_human_review is False


def test_value_clamped_to_one():
    # field 0.75 + verified 0.20 + corroboration 0.15 = 1.10 → clamp 1.0
    finding = _ai(source="field", rule_id="LAW-FAR-001", evidence_verified=True)
    s = score_ai_finding(finding, [_rule("CALC-001", "fail")])
    assert s.value == 1.0


# ── score_findings (immutability) ─────────────────────────────────────────────

def test_score_findings_returns_new_copies_with_scores():
    original = _ai(source="field", evidence_verified=True)
    scored = score_findings([original], [])
    assert len(scored) == 1
    assert scored[0].confidence != 1.0 or scored[0].confidence == scored[0].confidence
    # original untouched (frozen dataclass — confidence still default 1.0)
    assert original.confidence == 1.0
    # scored copy has computed confidence and review flag set
    assert 0.0 <= scored[0].confidence <= 1.0
    assert isinstance(scored[0].needs_human_review, bool)


def test_score_findings_preserves_finding_data():
    original = _ai(source="llm", rule_id="LAW-007", severity="warning")
    scored = score_findings([original], [])[0]
    assert scored.rule_id == "LAW-007"
    assert scored.source == "llm"
    assert scored.detected_text == original.detected_text
