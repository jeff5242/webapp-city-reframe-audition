"""Gold-set driver for the deterministic field validator.

Loads gold cases from JSON, builds `ExtractedFields`, runs
`field_auditor.validate_fields()`, and evaluates the produced rule_ids against
the annotated expectations. Fully offline — no LLM calls — so it runs in CI.

The LLM extraction step is deliberately NOT exercised here: gold cases provide
the extracted fields directly, isolating the *validation rules* under test.
A separate live harness can plug an LLM predictor into `harness.evaluate()`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Set, Tuple

from ..parsing_pipeline.field_auditor import (
    BonusItem,
    ConsentRatio,
    ExtractedFields,
    validate_fields,
)
from .harness import EvalResult, aggregate, evaluate

_DEFAULT_GOLD = Path(__file__).parent.parent.parent / "tests" / "gold" / "field_auditor_gold.json"


@dataclass(frozen=True)
class GoldCase:
    id: str
    description: str
    reg_year: str
    fields: ExtractedFields
    expected_rule_ids: Set[str]


def _build_fields(raw: dict) -> ExtractedFields:
    """Construct ExtractedFields from a gold-case 'fields' dict, filling defaults."""
    bonus_items = [
        BonusItem(
            name=str(b.get("name", "")),
            rate_pct=float(b["rate_pct"]),
            legal_basis=str(b.get("legal_basis", "")),
        )
        for b in raw.get("bonus_items", [])
    ]

    consent = None
    if raw.get("consent_ratio") is not None:
        cr = raw["consent_ratio"]
        consent = ConsentRatio(
            owner_count_pct=cr.get("owner_count_pct"),
            land_area_pct=cr.get("land_area_pct"),
            source_page=int(cr.get("source_page", 0)),
        )

    return ExtractedFields(
        bonus_items=bonus_items,
        bonus_total_pct=raw.get("bonus_total_pct"),
        consent_ratio=consent,
        application_date=raw.get("application_date"),
        affidavit_date=raw.get("affidavit_date"),
        power_of_attorney_date=raw.get("power_of_attorney_date"),
        implementer_name=raw.get("implementer_name"),
        land_area_sqm=raw.get("land_area_sqm"),
    )


def load_gold(path: Path = _DEFAULT_GOLD) -> List[GoldCase]:
    """Load and parse gold cases from *path*."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    cases: List[GoldCase] = []
    for c in data["cases"]:
        cases.append(GoldCase(
            id=c["id"],
            description=c.get("description", ""),
            reg_year=str(c.get("reg_year", "111")),
            fields=_build_fields(c.get("fields", {})),
            expected_rule_ids=set(c.get("expected_rule_ids", [])),
        ))
    return cases


def predict_case(case: GoldCase) -> Set[str]:
    """Run the field validator and return the set of produced rule_ids."""
    findings = validate_fields(case.fields, reg_year=case.reg_year)
    return {f.rule_id for f in findings}


def evaluate_gold(path: Path = _DEFAULT_GOLD) -> Tuple[EvalResult, List[Tuple[str, EvalResult]]]:
    """Evaluate all gold cases; return (micro-averaged result, per-case results)."""
    cases = load_gold(path)
    per_case: List[Tuple[str, EvalResult]] = []
    for case in cases:
        predicted = predict_case(case)
        per_case.append((case.id, evaluate(predicted, case.expected_rule_ids)))
    overall = aggregate(r for _, r in per_case)
    return overall, per_case
