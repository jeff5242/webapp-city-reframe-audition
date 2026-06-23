"""Phase 4: LLM-powered semantic audit with structured JSON output.

Injects relevant 111-year regulation wiki rules and few-shot error examples
into each prompt. Forces structured output via JSON Schema so findings can
be parsed deterministically and fed back to PyMuPDF for red-box annotation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal


@dataclass(frozen=True)
class LlmFinding:
    rule_id: str
    error_type: Literal["typo", "semantic_contradiction", "regulatory_violation"]
    severity: Literal["critical", "warning"]
    detected_text: str
    suggested_text: str
    reason: str
    page_number: int


def audit_chunks(
    chunks: list,
    wiki_rules: str,
    model: str = "claude-haiku-4-5-20251001",
) -> List[LlmFinding]:
    """Run LLM audit over *chunks* using *wiki_rules* as regulatory context.

    Uses Haiku by default (cost-efficient for per-chunk worker calls).
    Switch to Sonnet for critical-severity re-verification.

    Returns a list of LlmFinding sorted by page_number.
    Raises NotImplementedError until this phase is implemented.
    """
    raise NotImplementedError("Phase 4 LLM auditor — to be implemented")
