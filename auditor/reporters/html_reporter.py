from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

from jinja2 import Environment, FileSystemLoader

from ..models import AuditReport, Finding

_TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"

_STATUS_ORDER = {"fail": 0, "warn": 1, "skip": 2, "pass": 3}
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

# 與 annotator._page_from_evidence 同一組 regex：從「…第 N 頁」抽頁碼，
# 供報告產生頁碼跳轉連結（副總 #5 highlight 定位）。
_EVIDENCE_PAGE_RE = re.compile(r"第\s*(\d+)\s*頁")


def _sort_findings(findings: List[Finding]) -> List[Finding]:
    return sorted(
        findings,
        key=lambda f: (
            _STATUS_ORDER.get(f.status, 9),
            _SEVERITY_ORDER.get(f.severity, 9),
        ),
    )


def _evidence_page(evidence: Optional[str]) -> Optional[int]:
    """從 evidence 字串（如「審議資料表第 11 頁」）抽出頁碼，無則回 None。"""
    if not evidence:
        return None
    m = _EVIDENCE_PAGE_RE.search(evidence)
    return int(m.group(1)) if m else None


def generate_report(report: AuditReport, templates_dir: Optional[str] = None) -> str:
    tdir = templates_dir or str(_TEMPLATES_DIR)
    env = Environment(loader=FileSystemLoader(tdir), autoescape=True)
    env.filters["evidence_page"] = _evidence_page
    template = env.get_template("report.html")
    return template.render(
        report=report,
        sorted_findings=_sort_findings(report.findings),
    )
