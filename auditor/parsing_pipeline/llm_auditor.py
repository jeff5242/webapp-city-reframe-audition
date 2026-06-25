"""Phase 4: LLM-powered semantic audit with structured JSON output.

Injects relevant 111-year regulation wiki rules and few-shot error examples
into each prompt. Forces structured output via tool_use so findings can
be parsed deterministically and fed back to PyMuPDF for red-box annotation.

Model selection:
  - Claude Haiku 4.5  (default) — cost-efficient per-chunk worker
  - Claude Sonnet 4.6            — optional re-verification of critical findings
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import List, Literal, Sequence

log = logging.getLogger(__name__)

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_VERIFY_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 2048

ErrorType = Literal["typo", "semantic_contradiction", "regulatory_violation"]
Severity = Literal["critical", "warning"]


@dataclass(frozen=True)
class LlmFinding:
    rule_id: str
    error_type: ErrorType
    severity: Severity
    detected_text: str
    suggested_text: str
    reason: str
    page_number: int


# --- Tool / JSON schema for structured output ---

_AUDIT_TOOL = {
    "name": "report_findings",
    "description": (
        "Report all errors, terminology mistakes, and regulatory violations found "
        "in the urban-renewal document excerpt. Return an empty list if no issues."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "rule_id": {"type": "string", "description": "e.g. TERM-001, CONS-001, LAW-001"},
                        "error_type": {"type": "string", "enum": ["typo", "semantic_contradiction", "regulatory_violation"]},
                        "severity": {"type": "string", "enum": ["critical", "warning"]},
                        "detected_text": {"type": "string"},
                        "suggested_text": {"type": "string"},
                        "reason": {"type": "string"},
                        "page_number": {"type": "integer"},
                    },
                    "required": ["rule_id", "error_type", "severity", "detected_text",
                                 "suggested_text", "reason", "page_number"],
                },
            }
        },
        "required": ["findings"],
    },
}

_SYSTEM_PROMPT_TEMPLATE = """\
你是臺北市都市更新審議的專業審查員。請針對以下文件片段，依照臺北市111年都更法規執行審查，找出：
1. 錯別字或用詞錯誤（typo）
2. 語意或邏輯矛盾（semantic_contradiction）
3. 違反法規規定（regulatory_violation）

## 適用法規重點（Wiki 節錄）
{wiki_rules}

## 注意事項
- 每個發現必須引用原文（detected_text）
- rule_id 格式：TERM-NNN（用詞）、CONS-NNN（一致性）、LAW-NNN（法規）
- 頁碼（page_number）填入文件中的實際頁碼，若不確定填 0
- 僅回報確定有問題的內容，勿過度標記
"""


def _build_system_prompt(wiki_rules: str) -> str:
    return _SYSTEM_PROMPT_TEMPLATE.format(wiki_rules=wiki_rules)


def _parse_finding(raw: dict) -> LlmFinding | None:
    try:
        return LlmFinding(
            rule_id=str(raw["rule_id"]),
            error_type=raw["error_type"],
            severity=raw["severity"],
            detected_text=str(raw["detected_text"]),
            suggested_text=str(raw.get("suggested_text", "")),
            reason=str(raw["reason"]),
            page_number=int(raw.get("page_number", 0)),
        )
    except (KeyError, ValueError, TypeError) as exc:
        log.warning("Skipped malformed finding: %s — %s", raw, exc)
        return None


def _call_model(
    client,
    model: str,
    system: str,
    chunk_text: str,
) -> List[LlmFinding]:
    """Call Claude with tool_use and parse findings from the response."""
    response = client.messages.create(
        model=model,
        max_tokens=_MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": chunk_text}],
        tools=[_AUDIT_TOOL],
        tool_choice={"type": "any"},
    )

    findings: List[LlmFinding] = []
    for block in response.content:
        if block.type != "tool_use":
            continue
        raw_list = block.input.get("findings", [])
        if isinstance(raw_list, str):
            try:
                raw_list = json.loads(raw_list)
            except json.JSONDecodeError:
                continue
        for item in raw_list:
            f = _parse_finding(item)
            if f:
                findings.append(f)
    return findings


def audit_chunks(
    chunks: "Sequence",
    wiki_rules: str,
    model: str = _DEFAULT_MODEL,
    verify_critical: bool = False,
) -> List[LlmFinding]:
    """Run LLM audit over *chunks* using *wiki_rules* as regulatory context.

    Each chunk is audited independently by a Haiku worker.
    If *verify_critical* is True, critical findings are re-verified by Sonnet.

    Returns findings sorted by page_number.

    Raises ImportError if anthropic SDK is not installed.
    """
    try:
        import anthropic
    except ImportError:
        raise ImportError("anthropic SDK required. Install: pip install anthropic")

    client = anthropic.Anthropic()
    system = _build_system_prompt(wiki_rules)

    all_findings: List[LlmFinding] = []

    for chunk in chunks:
        chunk_text = chunk.text if hasattr(chunk, "text") else str(chunk)
        if not chunk_text.strip():
            continue
        try:
            findings = _call_model(client, model, system, chunk_text)
            all_findings.extend(findings)
        except Exception as exc:
            log.error("LLM audit failed for chunk %r: %s", getattr(chunk, "section_title", "?"), exc)

    if verify_critical:
        critical = [f for f in all_findings if f.severity == "critical"]
        if critical:
            verify_system = (
                system
                + "\n\n## 二次驗證模式\n請確認以下發現是否真的有問題，若誤判請回傳 findings=[]。"
            )
            verified: List[LlmFinding] = []
            non_critical = [f for f in all_findings if f.severity != "critical"]
            for finding in critical:
                try:
                    result = _call_model(
                        client,
                        _VERIFY_MODEL,
                        verify_system,
                        f"原始發現：{finding.detected_text}\n原因：{finding.reason}",
                    )
                    if result:
                        verified.append(finding)
                except Exception as exc:
                    log.error("Verification failed for %s: %s", finding.rule_id, exc)
                    verified.append(finding)  # keep on error
            all_findings = non_critical + verified

    return sorted(all_findings, key=lambda f: f.page_number)
