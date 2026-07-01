"""Phase 6: Composite confidence scoring for AI findings.

Combines three independent signals into a single confidence value and a
human-review routing decision:

1. **Source reliability** — deterministic checks (field validation, cross-doc
   comparison) start higher than free-form LLM semantic findings.
2. **Evidence grounding** (Phase 5) — a finding whose quote was confirmed in
   the source is more trustworthy; an unverified LLM quote is penalised.
3. **Cross-track corroboration** — when the classic rule engine (Track A) has
   flagged the same topic, agreement between the two independent systems raises
   confidence.

Findings below the review threshold, or critical findings without verified
evidence, are routed to human review (`needs_human_review=True`). The scorer is
pure: it returns new immutable AiFinding copies, never mutating the inputs.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import List, Sequence, Tuple

from ..models import AiFinding, Finding

# Base confidence by finding source.
_BASE_BY_SOURCE = {
    "field": 0.75,  # deterministic rule validation on extracted values
    "cross": 0.75,  # deterministic cross-document comparison
    "llm": 0.45,    # free-form semantic finding — least certain on its own
}

_EVIDENCE_BOOST = 0.20
_UNVERIFIED_LLM_PENALTY = 0.10
_CORROBORATION_BOOST = 0.15

# Below this, a finding is routed to human review.
_REVIEW_THRESHOLD = 0.60


@dataclass(frozen=True)
class ConfidenceScore:
    value: float                 # 0.0 – 1.0
    needs_human_review: bool
    factors: Tuple[str, ...]     # human-readable explanation of the score


def _topic(rule_id: str) -> str:
    """Map a rule_id (from either track) to a coarse topic for corroboration.

    Topics that appear in BOTH tracks (bonus, term) enable cross-track
    agreement detection; the rest are track-specific and simply won't match.
    """
    rid = (rule_id or "").upper()
    if "FAR" in rid or rid in {"CALC-001", "CALC-004"}:
        return "bonus"
    if rid in {"CALC-002", "CALC-003"}:
        return "parking"
    if "CON-" in rid:            # LAW-CON-001/002 (consent ratio)
        return "consent"
    if "DATE" in rid:
        return "date"
    if "PII" in rid:
        return "pii"
    if "TERM" in rid:
        return "term"
    if "AREA" in rid or "OWN" in rid or "VAL" in rid or "IMP" in rid:
        return "crossdoc"
    if "CONS" in rid:
        return "consistency"
    if "DOC" in rid:
        return "document"
    if "FORM" in rid:
        return "form"
    return "other"


def _is_corroborated(finding: AiFinding, rule_findings: Sequence[Finding]) -> bool:
    """True if a failing Track A rule shares this finding's topic."""
    topic = _topic(finding.rule_id)
    if topic == "other":
        return False
    return any(
        rf.status == "fail" and _topic(rf.rule_id) == topic
        for rf in rule_findings
    )


def score_ai_finding(
    finding: AiFinding,
    rule_findings: Sequence[Finding],
) -> ConfidenceScore:
    """Compute a composite confidence score for a single AI finding."""
    factors: List[str] = []
    base = _BASE_BY_SOURCE.get(finding.source, 0.50)
    factors.append(f"來源基準 {finding.source}（{base:.2f}）")

    if finding.evidence_verified:
        base += _EVIDENCE_BOOST
        factors.append(f"原文已核對（+{_EVIDENCE_BOOST:.2f}）")
    elif finding.source == "llm":
        base -= _UNVERIFIED_LLM_PENALTY
        factors.append(f"原文未核對（-{_UNVERIFIED_LLM_PENALTY:.2f}）")

    if _is_corroborated(finding, rule_findings):
        base += _CORROBORATION_BOOST
        factors.append(f"與規則引擎一致（+{_CORROBORATION_BOOST:.2f}）")

    value = max(0.0, min(1.0, base))

    needs_review = value < _REVIEW_THRESHOLD or (
        finding.severity == "critical" and not finding.evidence_verified
    )
    if needs_review:
        if value < _REVIEW_THRESHOLD:
            factors.append("信心低於門檻 → 需人工複核")
        else:
            factors.append("critical 且原文未核對 → 需人工複核")

    return ConfidenceScore(
        value=round(value, 2),
        needs_human_review=needs_review,
        factors=tuple(factors),
    )


def score_findings(
    ai_findings: Sequence[AiFinding],
    rule_findings: Sequence[Finding],
) -> List[AiFinding]:
    """Return new AiFinding copies with confidence + needs_human_review set.

    Pure: inputs are never mutated (AiFinding is frozen).
    """
    scored: List[AiFinding] = []
    for f in ai_findings:
        s = score_ai_finding(f, rule_findings)
        scored.append(replace(
            f,
            confidence=s.value,
            needs_human_review=s.needs_human_review,
        ))
    return scored
