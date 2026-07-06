"""HTML reporter: evidence page-jump filter + 分級圖例 rendering (副總 #4/#5)."""
from __future__ import annotations

from auditor.models import AuditReport, Finding
from auditor.reporters.html_reporter import _evidence_page, generate_report


def _report(findings, annotated_pdf_key=None):
    return AuditReport(
        case_name="測試案",
        audit_time="2026-07-06 10:00",
        rule_version="111年版",
        documents=["business_plan.pdf"],
        review_table=None,
        front_docs=None,
        pii_risks=[],
        term_matches=[],
        findings=findings,
        annotated_pdf_key=annotated_pdf_key,
    )


# ── _evidence_page filter ────────────────────────────────────────────────────

def test_evidence_page_extracts_page_number():
    assert _evidence_page("審議資料表第 11 頁") == 11


def test_evidence_page_tolerates_no_space():
    assert _evidence_page("第3頁") == 3


def test_evidence_page_returns_none_when_absent():
    assert _evidence_page("審議資料表") is None


def test_evidence_page_returns_none_for_empty():
    assert _evidence_page(None) is None
    assert _evidence_page("") is None


# ── Page-jump link (副總 #5) ─────────────────────────────────────────────────

def test_jump_link_rendered_when_page_and_key_present():
    f = Finding(
        rule_id="CALC-001", rule_name="容積獎勵", status="fail", severity="critical",
        message="超出上限", evidence="審議資料表第 11 頁",
    )
    html = generate_report(_report([f], annotated_pdf_key="abcd1234"))
    assert "/download/abcd1234#page=11" in html
    assert "看標註第 11 頁" in html


def test_no_jump_link_without_annotated_key():
    f = Finding(
        rule_id="CALC-001", rule_name="容積獎勵", status="fail", severity="critical",
        message="超出上限", evidence="審議資料表第 11 頁",
    )
    html = generate_report(_report([f], annotated_pdf_key=None))
    assert "#page=11" not in html
    # evidence 文字仍應顯示
    assert "審議資料表第 11 頁" in html


def test_no_jump_link_when_evidence_has_no_page():
    f = Finding(
        rule_id="DOC-001", rule_name="文件齊備", status="fail", severity="high",
        message="缺切結書", evidence="審議資料表",
    )
    html = generate_report(_report([f], annotated_pdf_key="abcd1234"))
    assert "#page=" not in html


# ── 分級圖例 + 白話命名 (副總 #4) ────────────────────────────────────────────

def test_legend_and_new_labels_present():
    findings = [
        Finding(rule_id="CALC-001", rule_name="嚴重項", status="fail",
                severity="critical", message="m"),
        Finding(rule_id="FORM-001", rule_name="重要項", status="fail",
                severity="high", message="m"),
        Finding(rule_id="FORM-002", rule_name="核對項", status="warn",
                severity="medium", message="m"),
    ]
    html = generate_report(_report(findings))
    # 圖例
    assert "分級說明" in html
    # 白話命名（副總 #4：必修→必檢；新增建議提醒）
    assert "必檢 · 應修正" in html
    assert "建議提醒 · 待人工核對" in html
    # 舊命名已移除
    assert "必修 · 應修正" not in html
