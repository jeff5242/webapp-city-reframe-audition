"""Tests for the evaluation harness and the field-validator gold set (gap 4)."""
from __future__ import annotations

import pytest

from auditor.eval.harness import EvalResult, aggregate, evaluate
from auditor.eval.field_gold import evaluate_gold, load_gold, predict_case


# ── harness metrics ───────────────────────────────────────────────────────────

def test_evaluate_perfect_match():
    r = evaluate({"A", "B"}, {"A", "B"})
    assert (r.tp, r.fp, r.fn) == (2, 0, 0)
    assert r.precision == 1.0
    assert r.recall == 1.0
    assert r.f1 == 1.0


def test_evaluate_false_positive():
    r = evaluate({"A", "B", "C"}, {"A", "B"})
    assert (r.tp, r.fp, r.fn) == (2, 1, 0)
    assert r.precision == pytest.approx(2 / 3)
    assert r.recall == 1.0


def test_evaluate_false_negative():
    r = evaluate({"A"}, {"A", "B"})
    assert (r.tp, r.fp, r.fn) == (1, 0, 1)
    assert r.precision == 1.0
    assert r.recall == 0.5


def test_evaluate_empty_both_is_perfect():
    """No expected and none predicted → precision/recall default to 1.0."""
    r = evaluate(set(), set())
    assert r.precision == 1.0
    assert r.recall == 1.0


def test_evaluate_false_positive_on_empty_expected():
    r = evaluate({"A"}, set())
    assert (r.tp, r.fp, r.fn) == (0, 1, 0)
    assert r.precision == 0.0


def test_aggregate_micro_averages():
    r1 = evaluate({"A"}, {"A", "B"})       # tp1 fn1
    r2 = evaluate({"C", "D"}, {"C"})       # tp1 fp1
    agg = aggregate([r1, r2])
    assert (agg.tp, agg.fp, agg.fn) == (2, 1, 1)


def test_f1_zero_when_no_overlap():
    r = evaluate({"X"}, {"Y"})
    assert r.f1 == 0.0


# ── gold set loading ──────────────────────────────────────────────────────────

def test_gold_set_loads():
    cases = load_gold()
    assert len(cases) >= 8
    ids = {c.id for c in cases}
    assert "bonus_over_cap_111" in ids
    assert "all_clean" in ids


def test_gold_set_has_clean_and_violation_cases():
    cases = load_gold()
    clean = [c for c in cases if not c.expected_rule_ids]
    violations = [c for c in cases if c.expected_rule_ids]
    assert clean, "gold set must include clean (no-finding) cases"
    assert violations, "gold set must include violation cases"


# ── field validator against gold ──────────────────────────────────────────────

def test_field_validator_meets_precision_recall_threshold():
    """The deterministic validator must score highly on the gold set.

    This is both a capability demonstration and a regression guard: changing a
    threshold in field_auditor without updating the gold set will fail here.
    """
    overall, per_case = evaluate_gold()
    assert overall.precision >= 0.9, f"precision too low: {overall.as_dict()}"
    assert overall.recall >= 0.9, f"recall too low: {overall.as_dict()}"


def test_reg_year_affects_prediction():
    """The same consent ratios must be clean for 111 but flagged for 113."""
    cases = {c.id: c for c in load_gold()}
    clean_111 = predict_case(cases["consent_ok_111_year"])
    flagged_113 = predict_case(cases["consent_ok_111_fails_113"])
    assert clean_111 == set()
    assert "LAW-CON-001" in flagged_113
    assert "LAW-CON-002" in flagged_113


def test_multiple_violations_case_detects_all():
    cases = {c.id: c for c in load_gold()}
    predicted = predict_case(cases["multiple_violations"])
    assert predicted == {"LAW-FAR-001", "LAW-CON-001", "LAW-CON-002", "CONS-DATE-001"}


def test_all_clean_case_produces_no_findings():
    cases = {c.id: c for c in load_gold()}
    assert predict_case(cases["all_clean"]) == set()
