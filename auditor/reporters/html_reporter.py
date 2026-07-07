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


def _fmt_area(v: Optional[float]) -> Optional[str]:
    return f"{v:,.2f} m²" if v is not None else None


def _fmt_count(v: Optional[int], unit: str = "輛") -> Optional[str]:
    return f"{v} {unit}" if v is not None else None


def key_numbers(report: AuditReport) -> List[dict]:
    """關鍵數字清單（副總 #2）：把驅動規則的數值集中，標明「系統從哪一頁抓的」。

    純地端模式無 API key，數值來自 Track A 擷取（審議資料表 / 報核日期來源），
    非 LLM。每筆帶 label / value / source / page，供報告顯示與頁碼跳轉。
    缺漏的關鍵欄位仍列出並標記，讓審核者知道哪些要人工補核。
    """
    rt = report.review_table
    page = rt.raw_page if rt else None
    rows: List[dict] = []

    # 報核日期（來源可能是審議資料表/申請書…，頁碼獨立）
    rows.append({
        "label": "報核日期",
        "value": report.report_date,
        "source": report.report_date_source or "—",
        "page": report.report_date_page,
        "missing": report.report_date is None,
    })

    if rt is not None:
        specs = [
            ("基準容積", _fmt_area(rt.base_floor_area), rt.base_floor_area is None),
            ("容積獎勵申請額度", _fmt_area(rt.bonus_floor_area), rt.bonus_floor_area is None),
            ("容積獎勵上限", _fmt_area(rt.bonus_limit), rt.bonus_limit is None),
            ("法定停車位", _fmt_count(rt.legal_parking), rt.legal_parking is None),
            ("實設停車位", _fmt_count(rt.actual_parking), rt.actual_parking is None),
            ("無障礙停車位", _fmt_count(rt.accessible_parking), rt.accessible_parking is None),
            ("充電車位", _fmt_count(rt.ev_parking), rt.ev_parking is None),
        ]
        for label, value, missing in specs:
            rows.append({
                "label": label,
                "value": value,
                "source": "審議資料表",
                "page": page,
                "missing": missing,
            })

    return rows


_ACTION_HEADINGS = [
    ("fail", "一、應修正項目（必檢）", "請實施者修正後再送。"),
    ("warn", "二、待人工核對項目（建議提醒）", "系統資料不足或需判讀，請承辦人工確認（非實施者違規）。"),
]


def audit_opinion_text(report: AuditReport) -> str:
    """審核意見結構化輸出（副總 #6）：通用條列式純文字，供承辦一鍵複製貼進意見書。

    格式：抬頭（案名/報核日期/適用版次/審查時間）+ 應修正項目 + 待人工核對項目
    + 通過項目統計。每項含 現況／核算／法源／建議，方便人工彙整。
    """
    lines: List[str] = []
    lines.append("臺北市都市更新審議 自動審查意見")
    lines.append(f"案名：{report.case_name}")
    if report.report_date:
        src = report.report_date_source or ""
        pg = f" 第{report.report_date_page}頁" if report.report_date_page else ""
        lines.append(f"報核日期：{report.report_date}（來源：{src}{pg}）→ 適用 {report.rule_version}")
    else:
        lines.append(f"適用版次：{report.rule_version}")
    lines.append(f"審查時間：{report.audit_time}")
    lines.append("")

    for status, heading, default_suggestion in _ACTION_HEADINGS:
        items = [f for f in _sort_findings(report.findings) if f.status == status]
        lines.append(heading)
        if not items:
            lines.append("　（無）")
        for idx, f in enumerate(items, 1):
            lines.append(f"{idx}. 【{f.rule_id}】{f.rule_name}")
            lines.append(f"　現況：{f.message}")
            if f.expected_calc or f.computed_result:
                calc = "；".join(x for x in (f.expected_calc, f.computed_result) if x)
                lines.append(f"　核算：{calc}")
            if f.reference:
                lines.append(f"　法源：{f.reference}")
            lines.append(f"　建議：{default_suggestion}")
        lines.append("")

    passes = [f for f in report.findings if f.status == "pass"]
    lines.append(f"三、通過項目：共 {len(passes)} 項，符合規定，無需處理。")
    return "\n".join(lines)


def generate_report(report: AuditReport, templates_dir: Optional[str] = None) -> str:
    tdir = templates_dir or str(_TEMPLATES_DIR)
    env = Environment(loader=FileSystemLoader(tdir), autoescape=True)
    env.filters["evidence_page"] = _evidence_page
    template = env.get_template("report.html")
    return template.render(
        report=report,
        sorted_findings=_sort_findings(report.findings),
        key_numbers=key_numbers(report),
        audit_opinion_text=audit_opinion_text(report),
    )
