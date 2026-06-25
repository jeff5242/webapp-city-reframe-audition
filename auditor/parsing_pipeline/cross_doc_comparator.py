"""Cross-document field comparison: 事業計畫書 vs 權利變換計畫書.

Extracts a shared set of fields from both documents, then diffs them.
Discrepancies in land area, owner count, and pre/post-renewal values are
common submission errors that single-document audit cannot catch.

Usage:
    from auditor.parsing_pipeline.cross_doc_comparator import compare_documents

    findings = compare_documents(business_plan_md, rights_exchange_md)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Literal, Optional

log = logging.getLogger(__name__)

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 2048

# Tolerance for floating-point land area comparison (m²)
_AREA_TOLERANCE_SQM = 0.5

# Tolerance for value comparison (萬元)
_VALUE_TOLERANCE_WAN = 1.0


# ── Shared field model ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SharedFields:
    """Fields that must be consistent across both documents."""
    doc_label: str                        # "事業計畫書" or "權利變換計畫書"
    land_area_sqm: Optional[float]        # 更新單元總面積 (m²)
    owner_count: Optional[int]            # 土地所有權人人數
    pre_renewal_value_wan: Optional[float]  # 更新前權利價值合計 (萬元)
    post_renewal_value_wan: Optional[float] # 更新後權利價值合計 (萬元)
    implementer_name: Optional[str]


@dataclass(frozen=True)
class CrossDocFinding:
    """A discrepancy found between the two documents."""
    field_name: str
    rule_id: str
    severity: Literal["critical", "warning"]
    business_plan_value: str
    rights_exchange_value: str
    reason: str

    def as_dict(self) -> dict:
        return {
            "field_name": self.field_name,
            "rule_id": self.rule_id,
            "severity": self.severity,
            "business_plan_value": self.business_plan_value,
            "rights_exchange_value": self.rights_exchange_value,
            "reason": self.reason,
        }


# ── LLM extraction tool ───────────────────────────────────────────────────────

_EXTRACT_TOOL = {
    "name": "extract_shared_fields",
    "description": "Extract key fields that appear in both 事業計畫書 and 權利變換計畫書.",
    "input_schema": {
        "type": "object",
        "required": ["land_area_sqm", "owner_count", "pre_renewal_value_wan",
                     "post_renewal_value_wan", "implementer_name"],
        "properties": {
            "land_area_sqm": {
                "type": ["number", "null"],
                "description": "更新單元總面積（平方公尺），例如 1234.56。若未找到則為 null。",
            },
            "owner_count": {
                "type": ["integer", "null"],
                "description": "土地所有權人人數（整數）。若未找到則為 null。",
            },
            "pre_renewal_value_wan": {
                "type": ["number", "null"],
                "description": "更新前各所有權人權利價值合計（萬元）。若未找到則為 null。",
            },
            "post_renewal_value_wan": {
                "type": ["number", "null"],
                "description": "更新後各所有權人可分配權利價值合計（萬元）。若未找到則為 null。",
            },
            "implementer_name": {
                "type": ["string", "null"],
                "description": "實施者名稱（公司名稱）。若未找到則為 null。",
            },
        },
    },
}

_SYSTEM_PROMPT = """\
你是臺北市都市更新文件審查員。
請從以下 Markdown 文件中抽取指定欄位的數值。

## 抽取原則
- 僅回傳文件中**明確出現**的數字；若找不到則回傳 null。
- 面積單位統一換算為平方公尺（m²）。
- 金額單位統一換算為萬元。
- 若文件中出現多個版本的數值，取**最後出現**的版本（最新版）。
- 請勿推算或估計未出現的數值。
"""


# ── Extraction ────────────────────────────────────────────────────────────────


def _extract_fields(markdown: str, doc_label: str, client, model: str) -> SharedFields:
    text = markdown[:8000] if len(markdown) > 8000 else markdown
    try:
        response = client.messages.create(
            model=model,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": text}],
            tools=[_EXTRACT_TOOL],
            tool_choice={"type": "tool", "name": "extract_shared_fields"},
        )
    except Exception as exc:
        log.error("LLM extraction failed for %s: %s", doc_label, exc)
        return SharedFields(doc_label=doc_label, land_area_sqm=None, owner_count=None,
                            pre_renewal_value_wan=None, post_renewal_value_wan=None,
                            implementer_name=None)

    raw: dict = {}
    for block in response.content:
        if block.type == "tool_use" and block.name == "extract_shared_fields":
            raw = block.input
            break

    return SharedFields(
        doc_label=doc_label,
        land_area_sqm=_opt_float(raw.get("land_area_sqm")),
        owner_count=_opt_int(raw.get("owner_count")),
        pre_renewal_value_wan=_opt_float(raw.get("pre_renewal_value_wan")),
        post_renewal_value_wan=_opt_float(raw.get("post_renewal_value_wan")),
        implementer_name=_opt_str(raw.get("implementer_name")),
    )


# ── Comparison rules ──────────────────────────────────────────────────────────


def _compare(
    bp: SharedFields,
    re: SharedFields,
) -> List[CrossDocFinding]:
    findings: List[CrossDocFinding] = []

    # 土地面積
    if bp.land_area_sqm is not None and re.land_area_sqm is not None:
        diff = abs(bp.land_area_sqm - re.land_area_sqm)
        if diff > _AREA_TOLERANCE_SQM:
            findings.append(CrossDocFinding(
                field_name="更新單元總面積",
                rule_id="CONS-AREA-001",
                severity="critical",
                business_plan_value=f"{bp.land_area_sqm:.2f} m²",
                rights_exchange_value=f"{re.land_area_sqm:.2f} m²",
                reason=(
                    f"兩份文件的更新單元總面積差異 {diff:.2f} m²，"
                    "超過允許誤差 0.5 m²。請確認基地範圍及面積計算一致。"
                ),
            ))

    # 所有權人人數
    if bp.owner_count is not None and re.owner_count is not None:
        if bp.owner_count != re.owner_count:
            findings.append(CrossDocFinding(
                field_name="土地所有權人人數",
                rule_id="CONS-OWN-001",
                severity="critical",
                business_plan_value=f"{bp.owner_count} 人",
                rights_exchange_value=f"{re.owner_count} 人",
                reason=(
                    f"事業計畫書所有權人 {bp.owner_count} 人，"
                    f"權利變換計畫書 {re.owner_count} 人，數量不一致。"
                    "請核對所有權人名冊及同意書。"
                ),
            ))

    # 更新前權利價值
    if bp.pre_renewal_value_wan is not None and re.pre_renewal_value_wan is not None:
        diff = abs(bp.pre_renewal_value_wan - re.pre_renewal_value_wan)
        if diff > _VALUE_TOLERANCE_WAN:
            findings.append(CrossDocFinding(
                field_name="更新前權利價值合計",
                rule_id="CONS-VAL-001",
                severity="warning",
                business_plan_value=f"{bp.pre_renewal_value_wan:.1f} 萬元",
                rights_exchange_value=f"{re.pre_renewal_value_wan:.1f} 萬元",
                reason=(
                    f"更新前權利價值合計差異 {diff:.1f} 萬元。"
                    "兩份文件應使用同一估價報告書數值。"
                ),
            ))

    # 更新後權利價值
    if bp.post_renewal_value_wan is not None and re.post_renewal_value_wan is not None:
        diff = abs(bp.post_renewal_value_wan - re.post_renewal_value_wan)
        if diff > _VALUE_TOLERANCE_WAN:
            findings.append(CrossDocFinding(
                field_name="更新後權利價值合計",
                rule_id="CONS-VAL-002",
                severity="warning",
                business_plan_value=f"{bp.post_renewal_value_wan:.1f} 萬元",
                rights_exchange_value=f"{re.post_renewal_value_wan:.1f} 萬元",
                reason=(
                    f"更新後可分配價值合計差異 {diff:.1f} 萬元。"
                    "請確認分配計算表與事業計畫書採用相同估算基礎。"
                ),
            ))

    # 實施者名稱
    if bp.implementer_name and re.implementer_name:
        if bp.implementer_name.strip() != re.implementer_name.strip():
            findings.append(CrossDocFinding(
                field_name="實施者名稱",
                rule_id="CONS-IMP-001",
                severity="critical",
                business_plan_value=bp.implementer_name,
                rights_exchange_value=re.implementer_name,
                reason=(
                    "兩份文件的實施者名稱不一致。"
                    "請確認公司全名（含「股份有限公司」等）在所有文件中一致。"
                ),
            ))

    return findings


# ── Public API ────────────────────────────────────────────────────────────────


def compare_documents(
    business_plan_md: str,
    rights_exchange_md: str,
    model: str = _DEFAULT_MODEL,
) -> List[CrossDocFinding]:
    """Compare shared fields between 事業計畫書 and 權利變換計畫書 Markdown.

    Both Markdown strings should come from Phase 2a (Docling) output.
    Returns a list of CrossDocFinding (empty if no discrepancies found).
    Raises ImportError if anthropic SDK is not installed.
    """
    try:
        import anthropic
    except ImportError:
        raise ImportError("anthropic SDK required. Install: pip install anthropic")

    client = anthropic.Anthropic()

    bp_fields = _extract_fields(business_plan_md, "事業計畫書", client, model)
    re_fields = _extract_fields(rights_exchange_md, "權利變換計畫書", client, model)

    log.info(
        "Extracted — 事業計畫書: area=%s owner=%s | 權利變換: area=%s owner=%s",
        bp_fields.land_area_sqm, bp_fields.owner_count,
        re_fields.land_area_sqm, re_fields.owner_count,
    )

    return _compare(bp_fields, re_fields)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _opt_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _opt_int(v) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def _opt_str(v) -> Optional[str]:
    s = str(v).strip() if v is not None else ""
    return s if s else None
