"""Phase 4b: Structured field extraction and rule validation.

Extracts key regulatory fields from urban renewal document Markdown and
validates them against 111-year Taipei rules:

  - 容積獎勵合計上限  ≤ 40%（都更條例第65條）
  - 同意比率          土地所有權人 ≥ 2/3、土地面積 ≥ 3/4（重建）
  - 報核日期          申請書 / 切結書 / 委託書 三者一致

Designed to complement llm_auditor.py: whereas that module scans every
chunk for arbitrary errors, this module hunts for specific regulatory
violations in high-value fields.

Model selection follows the same policy as llm_auditor:
  default → Claude Haiku 4.5 (cost-efficient extraction worker)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import List, Literal, Optional

log = logging.getLogger(__name__)

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 2048

# ── Data model ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BonusItem:
    name: str
    rate_pct: float    # e.g. 10.0 for 10%
    legal_basis: str   # e.g. "都更條例第65條第1項第1款"


@dataclass(frozen=True)
class ConsentRatio:
    owner_count_pct: Optional[float]    # 土地所有權人人數比率 %
    land_area_pct: Optional[float]      # 土地面積比率 %
    source_page: int


@dataclass(frozen=True)
class ExtractedFields:
    """All structured fields extracted from a single document."""
    bonus_items: List[BonusItem]
    bonus_total_pct: Optional[float]          # stated total; None if not found
    consent_ratio: Optional[ConsentRatio]
    application_date: Optional[str]           # 申請書報核日期 (e.g. "2025-05-06")
    affidavit_date: Optional[str]             # 切結書日期
    power_of_attorney_date: Optional[str]     # 委託書日期
    implementer_name: Optional[str]
    land_area_sqm: Optional[float]            # 更新單元面積 m²


@dataclass
class FieldFinding:
    """A regulatory violation found during field validation."""
    field_name: str
    rule_id: str
    severity: Literal["critical", "warning"]
    actual_value: str
    expected: str
    reason: str
    page_number: int = 0

    def as_dict(self) -> dict:
        return {
            "field_name": self.field_name,
            "rule_id": self.rule_id,
            "severity": self.severity,
            "actual_value": self.actual_value,
            "expected": self.expected,
            "reason": self.reason,
            "page_number": self.page_number,
        }


# ── Extraction tool schema ────────────────────────────────────────────────────

_EXTRACT_TOOL = {
    "name": "extract_fields",
    "description": (
        "Extract key regulatory fields from a Taipei urban renewal document. "
        "Return null for any field not found in the text."
    ),
    "input_schema": {
        "type": "object",
        "required": ["bonus_items", "consent_ratio", "dates", "implementer_name", "land_area_sqm"],
        "properties": {
            "bonus_items": {
                "type": "array",
                "description": "容積獎勵項目清單（從事業計畫書第玖章）",
                "items": {
                    "type": "object",
                    "required": ["name", "rate_pct", "legal_basis"],
                    "properties": {
                        "name":        {"type": "string", "description": "獎勵項目名稱"},
                        "rate_pct":    {"type": "number", "description": "獎勵比率（%），例如 10.0"},
                        "legal_basis": {"type": "string", "description": "法令依據條款"},
                    },
                },
            },
            "bonus_total_pct": {
                "type": ["number", "null"],
                "description": "容積獎勵合計比率（%）。若文件未列合計則為 null。",
            },
            "consent_ratio": {
                "type": ["object", "null"],
                "description": "同意比率（來自現況分析章節）",
                "properties": {
                    "owner_count_pct": {"type": ["number", "null"], "description": "土地所有權人人數同意比率 %"},
                    "land_area_pct":   {"type": ["number", "null"], "description": "土地面積同意比率 %"},
                    "source_page":     {"type": "integer",          "description": "資料所在頁碼"},
                },
            },
            "dates": {
                "type": "object",
                "description": "三份前置文件的報核日期",
                "properties": {
                    "application_date":        {"type": ["string", "null"], "description": "申請書日期，格式 YYYY-MM-DD"},
                    "affidavit_date":          {"type": ["string", "null"], "description": "切結書日期"},
                    "power_of_attorney_date":  {"type": ["string", "null"], "description": "委託書日期"},
                },
            },
            "implementer_name": {
                "type": ["string", "null"],
                "description": "實施者（公司名稱），例如「測試建設股份有限公司」",
            },
            "land_area_sqm": {
                "type": ["number", "null"],
                "description": "更新單元總面積（平方公尺），例如 1234.56",
            },
        },
    },
}

_SYSTEM_PROMPT = """\
你是臺北市都市更新事業計畫審查員，專責從文件 Markdown 中精確抽取以下欄位。

## 抽取原則
- 只抽取文件中**明確出現**的數字與文字；若找不到則回傳 null。
- 容積獎勵比率以**基準容積的百分比**計算（例如「10%」→ rate_pct: 10.0）。
- 日期格式統一轉為 YYYY-MM-DD（民國年 → 西元年：民國 114 年 = 2025 年）。
- 同意比率取最新版（若有多次更新，取最後一次）。
- 若同一欄位出現多個數值，取文件中**最後出現**的值（最新版）。
"""


# ── Validation rules ──────────────────────────────────────────────────────────

# 都更條例第65條：容積獎勵合計上限（各版本相同）
_BONUS_CAP_PCT = 40.0

# 都更條例第22條重建同意門檻，依版本年度不同：
#   107/108/111年：土地所有權人 ≥ 2/3、土地面積 ≥ 3/4
#   113年        ：土地所有權人 ≥ 4/5、土地面積 ≥ 4/5（113年1月修正）
_CONSENT_THRESHOLDS: dict = {
    "107": (200.0 / 3, 75.0),   # owner ≥ 2/3, area ≥ 3/4
    "108": (200.0 / 3, 75.0),
    "111": (200.0 / 3, 75.0),
    "113": (80.0, 80.0),        # owner ≥ 4/5, area ≥ 4/5
}
_DEFAULT_REG_YEAR = "111"

# Module-level constants kept for backwards compatibility with existing tests
_CONSENT_OWNER_MIN_PCT = 200.0 / 3
_CONSENT_AREA_MIN_PCT = 75.0


def _consent_thresholds(reg_year: str) -> tuple[float, float]:
    """Return (owner_min_pct, area_min_pct) for *reg_year*."""
    return _CONSENT_THRESHOLDS.get(reg_year, _CONSENT_THRESHOLDS[_DEFAULT_REG_YEAR])


def validate_fields(
    fields: ExtractedFields,
    reg_year: str = _DEFAULT_REG_YEAR,
) -> List[FieldFinding]:
    """Apply regulatory rules for *reg_year* to extracted fields.

    *reg_year* must be one of "107", "108", "111", "113".
    Unknown years fall back to 111-year thresholds.

    Returns a list of FieldFinding; empty list means no violations found.
    """
    findings: List[FieldFinding] = []
    owner_min, area_min = _consent_thresholds(reg_year)

    # ── 容積獎勵上限 ──────────────────────────────────────────────────────
    if fields.bonus_total_pct is not None:
        if fields.bonus_total_pct > _BONUS_CAP_PCT:
            findings.append(FieldFinding(
                field_name="容積獎勵合計",
                rule_id="LAW-FAR-001",
                severity="critical",
                actual_value=f"{fields.bonus_total_pct:.1f}%",
                expected=f"≤ {_BONUS_CAP_PCT:.0f}%（都更條例第65條）",
                reason=(
                    f"容積獎勵合計 {fields.bonus_total_pct:.1f}% 超過法定上限 "
                    f"{_BONUS_CAP_PCT:.0f}%。請重新核算各獎勵項目比率。"
                ),
            ))
    elif fields.bonus_items:
        computed = sum(b.rate_pct for b in fields.bonus_items)
        if computed > _BONUS_CAP_PCT:
            findings.append(FieldFinding(
                field_name="容積獎勵合計（推算）",
                rule_id="LAW-FAR-001",
                severity="critical",
                actual_value=f"{computed:.1f}%（各項合計）",
                expected=f"≤ {_BONUS_CAP_PCT:.0f}%（都更條例第65條）",
                reason=(
                    f"各獎勵項目加總 {computed:.1f}% 超過法定上限，"
                    "且文件未列合計欄位，可能有漏報。"
                ),
            ))

    # ── 同意比率 ──────────────────────────────────────────────────────────
    if fields.consent_ratio is not None:
        cr = fields.consent_ratio
        if cr.owner_count_pct is not None and cr.owner_count_pct < owner_min:
            findings.append(FieldFinding(
                field_name="土地所有權人同意比率",
                rule_id="LAW-CON-001",
                severity="critical",
                actual_value=f"{cr.owner_count_pct:.2f}%",
                expected=f"≥ {owner_min:.2f}%（都更條例第22條第1項）",
                reason=(
                    f"土地所有權人同意比率 {cr.owner_count_pct:.2f}% "
                    f"未達法定門檻 {owner_min:.2f}%。"
                ),
                page_number=cr.source_page,
            ))
        if cr.land_area_pct is not None and cr.land_area_pct < area_min:
            findings.append(FieldFinding(
                field_name="土地面積同意比率",
                rule_id="LAW-CON-002",
                severity="critical",
                actual_value=f"{cr.land_area_pct:.2f}%",
                expected=f"≥ {area_min:.0f}%（都更條例第22條第1項）",
                reason=(
                    f"土地面積同意比率 {cr.land_area_pct:.2f}% "
                    f"未達法定門檻 {area_min:.0f}%。"
                ),
                page_number=cr.source_page,
            ))

    # ── 報核日期一致性 ────────────────────────────────────────────────────
    dates = {
        "申請書": fields.application_date,
        "切結書": fields.affidavit_date,
        "委託書": fields.power_of_attorney_date,
    }
    non_null = {k: v for k, v in dates.items() if v is not None}
    unique_dates = set(non_null.values())
    if len(unique_dates) > 1:
        date_summary = "、".join(f"{k}={v}" for k, v in non_null.items())
        findings.append(FieldFinding(
            field_name="報核日期一致性",
            rule_id="CONS-DATE-001",
            severity="warning",
            actual_value=date_summary,
            expected="三份文件日期應相同",
            reason=(
                "申請書、切結書、委託書的報核日期不一致。"
                "依審議慣例三份文件應填寫同一日期。"
            ),
        ))

    return findings


# ── LLM extraction ────────────────────────────────────────────────────────────

def extract_and_validate(
    markdown: str,
    model: str = _DEFAULT_MODEL,
    reg_year: str = _DEFAULT_REG_YEAR,
) -> tuple[ExtractedFields, List[FieldFinding]]:
    """Extract structured fields from *markdown* and validate against rules.

    Returns (ExtractedFields, List[FieldFinding]).
    Raises ImportError if anthropic SDK is not installed.
    """
    try:
        import anthropic
    except ImportError:
        raise ImportError("anthropic SDK required. Install: pip install anthropic")

    client = anthropic.Anthropic()

    # Truncate to stay within token budget (~6k chars ≈ 1.5k tokens input)
    text = markdown[:6000] if len(markdown) > 6000 else markdown

    response = client.messages.create(
        model=model,
        max_tokens=_MAX_TOKENS,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": text}],
        tools=[_EXTRACT_TOOL],
        tool_choice={"type": "tool", "name": "extract_fields"},
    )

    raw: dict = {}
    for block in response.content:
        if block.type == "tool_use" and block.name == "extract_fields":
            raw = block.input
            break

    extracted = _parse_extracted(raw)
    findings = validate_fields(extracted, reg_year=reg_year)
    return extracted, findings


def _parse_extracted(raw: dict) -> ExtractedFields:
    bonus_items: List[BonusItem] = []
    for item in raw.get("bonus_items") or []:
        try:
            bonus_items.append(BonusItem(
                name=str(item["name"]),
                rate_pct=float(item["rate_pct"]),
                legal_basis=str(item.get("legal_basis", "")),
            ))
        except (KeyError, ValueError, TypeError) as exc:
            log.debug("Skipped malformed bonus item %s: %s", item, exc)

    cr_raw = raw.get("consent_ratio") or {}
    consent_ratio: Optional[ConsentRatio] = None
    if cr_raw:
        try:
            consent_ratio = ConsentRatio(
                owner_count_pct=_opt_float(cr_raw.get("owner_count_pct")),
                land_area_pct=_opt_float(cr_raw.get("land_area_pct")),
                source_page=int(cr_raw.get("source_page", 0)),
            )
        except (ValueError, TypeError) as exc:
            log.debug("Malformed consent_ratio: %s", exc)

    dates = raw.get("dates") or {}
    return ExtractedFields(
        bonus_items=bonus_items,
        bonus_total_pct=_opt_float(raw.get("bonus_total_pct")),
        consent_ratio=consent_ratio,
        application_date=_opt_str(dates.get("application_date")),
        affidavit_date=_opt_str(dates.get("affidavit_date")),
        power_of_attorney_date=_opt_str(dates.get("power_of_attorney_date")),
        implementer_name=_opt_str(raw.get("implementer_name")),
        land_area_sqm=_opt_float(raw.get("land_area_sqm")),
    )


def _opt_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _opt_str(v) -> Optional[str]:
    if v is None or str(v).strip() == "":
        return None
    return str(v).strip()
