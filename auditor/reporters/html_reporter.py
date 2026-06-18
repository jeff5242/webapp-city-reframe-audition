from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from jinja2 import Environment, FileSystemLoader

from ..models import AuditReport, Finding

_TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"

_STATUS_ORDER = {"fail": 0, "warn": 1, "skip": 2, "pass": 3}
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def _sort_findings(findings: List[Finding]) -> List[Finding]:
    return sorted(
        findings,
        key=lambda f: (
            _STATUS_ORDER.get(f.status, 9),
            _SEVERITY_ORDER.get(f.severity, 9),
        ),
    )


def generate_report(report: AuditReport, templates_dir: Optional[str] = None) -> str:
    tdir = templates_dir or str(_TEMPLATES_DIR)
    env = Environment(loader=FileSystemLoader(tdir), autoescape=True)
    template = env.get_template("report.html")
    return template.render(
        report=report,
        sorted_findings=_sort_findings(report.findings),
    )
