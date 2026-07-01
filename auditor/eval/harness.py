"""Evaluation harness: precision / recall / F1 over label sets.

Generic and predictor-agnostic. A "prediction" and an "expectation" are each a
set of labels (here: rule_ids that should fire for a document). The harness
compares them and aggregates across cases with micro-averaging.

This is the measurement foundation the roadmap calls for (gap 4): it lets us
quantify how well any auditor — the deterministic field validator today, an
LLM auditor later — matches a human-annotated gold set. Runs offline with zero
API cost when driven by the deterministic validators.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Set


@dataclass(frozen=True)
class EvalResult:
    """Counts and derived metrics for one or more evaluated cases."""
    tp: int
    fp: int
    fn: int

    @property
    def precision(self) -> float:
        denom = self.tp + self.fp
        return self.tp / denom if denom else 1.0

    @property
    def recall(self) -> float:
        denom = self.tp + self.fn
        return self.tp / denom if denom else 1.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    def as_dict(self) -> dict:
        return {
            "tp": self.tp, "fp": self.fp, "fn": self.fn,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
        }


def evaluate(predicted: Set[str], expected: Set[str]) -> EvalResult:
    """Compare a single case's predicted labels against expected labels."""
    predicted = set(predicted)
    expected = set(expected)
    tp = len(predicted & expected)
    fp = len(predicted - expected)
    fn = len(expected - predicted)
    return EvalResult(tp=tp, fp=fp, fn=fn)


def aggregate(results: Iterable[EvalResult]) -> EvalResult:
    """Micro-average: sum tp/fp/fn across cases, then derive metrics."""
    tp = fp = fn = 0
    for r in results:
        tp += r.tp
        fp += r.fp
        fn += r.fn
    return EvalResult(tp=tp, fp=fp, fn=fn)
