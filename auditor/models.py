from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional, Tuple, List, TYPE_CHECKING


@dataclass(frozen=True)
class PiiRisk:
    page: int
    risk_type: str  # "phone", "residential_address", "id_number"
    value: str
    context: str
    severity: Literal["HIGH", "MEDIUM", "LOW"]


@dataclass(frozen=True)
class FrontDoc:
    doc_type: str  # "申請書", "切結書", "委託書", "審議資料表"
    page: int
    purpose: Optional[str] = None  # for 委託書: "都更規劃", "建築設計", etc.


@dataclass(frozen=True)
class FrontDocsData:
    docs: Tuple[FrontDoc, ...]
    poa_count: int
    has_application: bool
    has_affidavit: bool
    has_review_table: bool
    report_date: Optional[str] = None       # ROC date string from 申請書/切結書/委託書
    report_date_page: Optional[int] = None  # page number where date was found
    report_date_source: Optional[str] = None  # doc_type: "申請書", "切結書", "委託書"


@dataclass(frozen=True)
class ReviewTableData:
    case_name: Optional[str]
    implementer: Optional[str]
    implementer_id: Optional[str]
    submission_type: Optional[str]  # "A-1", "B-1", "B-2", "C", "D"
    fill_date: Optional[str]
    land_area: Optional[float]
    base_floor_area: Optional[float]
    bonus_floor_area: Optional[float]
    bonus_limit: Optional[float]
    legal_parking: Optional[int]
    actual_parking: Optional[int]
    accessible_parking: Optional[int]
    ev_parking: Optional[int]
    owner_consent_ratio: Optional[float]
    raw_page: Optional[int]
    # 報核日期：取自審議資料表「辦理過程」的「報核」列（最新一筆）。
    # 版本選擇優先採用此日期，第一次申請無審議資料表時才退回申請書日期。
    report_filing_date: Optional[str] = None


@dataclass(frozen=True)
class WrongTermMatch:
    page: int
    wrong_term: str
    correct_term: str
    context: str


@dataclass(frozen=True)
class NumberContext:
    page: int
    field: str   # "accessible_parking", "legal_parking", "bonus_floor_area"
    value: float
    raw_text: str


@dataclass(frozen=True)
class FindingDiff:
    rule_id: str
    rule_name: str
    change: Literal["improved", "regressed", "changed", "new"]
    prev_status: Optional[str]
    curr_status: str
    message: str


@dataclass(frozen=True)
class Finding:
    rule_id: str
    rule_name: str
    status: Literal["pass", "fail", "warn", "skip"]
    severity: Literal["critical", "high", "medium", "low", "info"]
    message: str
    evidence: Optional[str] = None
    reference: Optional[str] = None


@dataclass(frozen=True)
class AuditData:
    review_table: Optional[ReviewTableData]
    front_docs: Optional[FrontDocsData]
    pii_risks: Tuple[PiiRisk, ...]
    term_matches: Tuple[WrongTermMatch, ...] = field(default_factory=tuple)
    number_contexts: Tuple[NumberContext, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AiFinding:
    """Unified AI-generated finding from Track B pipeline."""
    source: Literal["llm", "field", "cross"]
    rule_id: str
    severity: Literal["critical", "warning"]
    field_name: str       # error_type (llm), field_name (field), or field_name (cross-doc)
    detected_text: str    # detected_text, actual_value, or "計畫書 A vs B" summary
    reason: str
    page_number: int = 0
    # Evidence grounding (Phase 5): whether detected_text was confirmed to
    # appear in the source document, and the grounded quote (from offline
    # verification or the Anthropic Citations API).
    evidence_text: Optional[str] = None
    evidence_verified: bool = False
    # Confidence scoring (Phase 6): composite confidence in [0,1] and whether
    # the finding should be routed to a human reviewer.
    confidence: float = 1.0
    needs_human_review: bool = False


@dataclass
class AuditReport:
    case_name: str
    audit_time: str
    rule_version: str
    documents: List[str]
    review_table: Optional[ReviewTableData]
    front_docs: Optional[FrontDocsData]
    pii_risks: List[PiiRisk]
    term_matches: List[WrongTermMatch]
    findings: List[Finding]
    # ① 報核日期相關（優先從 申請書/切結書/委託書 讀取，fallback 審議資料表填表日期）
    fill_date_iso: Optional[str] = None
    report_date: Optional[str] = None         # 報核日期 ROC string
    report_date_source: Optional[str] = None  # "申請書", "切結書", "委託書", or "審議資料表（填表日期）"
    report_date_page: Optional[int] = None
    # ② 版次差異比對
    diffs: List[FindingDiff] = field(default_factory=list)
    prev_audit_time: Optional[str] = None
    # ③ PDF 標註下載 key
    annotated_pdf_key: Optional[str] = None
    # ④ Track B AI pipeline findings (empty when ANTHROPIC_API_KEY not set)
    ai_findings: List["AiFinding"] = field(default_factory=list)
    # ⑤ Peer comparison stats (None when fewer than 2 same-type cases exist)
    peer_stats: Optional[dict] = None

    @property
    def critical_fails(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == "critical" and f.status == "fail"]

    @property
    def high_fails(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == "high" and f.status == "fail"]

    @property
    def warnings(self) -> List[Finding]:
        return [f for f in self.findings if f.status == "warn"]

    @property
    def passes(self) -> List[Finding]:
        return [f for f in self.findings if f.status == "pass"]

    @property
    def high_risk_pii(self) -> List[PiiRisk]:
        return [p for p in self.pii_risks if p.severity == "HIGH"]
