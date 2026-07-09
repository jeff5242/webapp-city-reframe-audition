"""HTML reporter: page-jump, 分級圖例, 關鍵數字, 審核意見 (副總 #2/#4/#5/#6)."""
from __future__ import annotations

from auditor.models import AuditReport, Finding, ReviewTableData
from auditor.reporters.html_reporter import (
    _evidence_page,
    audit_opinion_text,
    generate_report,
    key_numbers,
)


def _rt(**overrides):
    base = dict(
        case_name="測試案", implementer="○○建設", implementer_id="00000000",
        submission_type="B-1", fill_date="112年12月26日", land_area=1050.0,
        base_floor_area=3755.25, bonus_floor_area=1928.58, bonus_limit=1877.63,
        legal_parking=58, actual_parking=108, accessible_parking=2, ev_parking=6,
        owner_consent_ratio=None, raw_page=11, report_filing_date="112年12月26日",
    )
    base.update(overrides)
    return ReviewTableData(**base)


def _report(findings, annotated_pdf_key=None, review_table=None,
            report_date=None, report_date_source=None, report_date_page=None,
            evidence_images=None, report_date_secondary=None,
            report_date_secondary_source=None):
    return AuditReport(
        case_name="測試案",
        audit_time="2026-07-06 10:00",
        rule_version="111年版",
        documents=["business_plan.pdf"],
        review_table=review_table,
        front_docs=None,
        pii_risks=[],
        term_matches=[],
        findings=findings,
        annotated_pdf_key=annotated_pdf_key,
        evidence_images=evidence_images or {},
        report_date=report_date,
        report_date_source=report_date_source,
        report_date_page=report_date_page,
        report_date_secondary=report_date_secondary,
        report_date_secondary_source=report_date_secondary_source,
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


def test_download_button_forces_attachment_but_jump_link_inline():
    f = Finding(
        rule_id="CALC-001", rule_name="容積獎勵", status="fail", severity="critical",
        message="超出上限", evidence="審議資料表第 11 頁",
    )
    html = generate_report(_report([f], annotated_pdf_key="abcd1234"))
    # 下載按鈕強制 attachment
    assert "/download/abcd1234?dl=1" in html
    # 定位連結不帶 dl（走 inline，瀏覽器開新頁跳頁）
    assert "/download/abcd1234#page=11" in html


# ── 標註頁截圖內嵌（副總 UX highlight 定位）─────────────────────────────────

def test_evidence_thumbnail_embedded_when_image_present():
    f = Finding(
        rule_id="CALC-001", rule_name="容積獎勵", status="fail", severity="critical",
        message="超出上限", evidence="審議資料表第 11 頁",
    )
    html = generate_report(_report(
        [f], annotated_pdf_key="abcd1234",
        evidence_images={11: "data:image/jpeg;base64,Zm9v"},
    ))
    assert 'src="data:image/jpeg;base64,Zm9v"' in html
    assert "第 11 頁標註預覽" in html


def test_no_thumbnail_when_page_not_in_images():
    f = Finding(
        rule_id="CALC-001", rule_name="容積獎勵", status="fail", severity="critical",
        message="超出上限", evidence="審議資料表第 11 頁",
    )
    html = generate_report(_report(
        [f], annotated_pdf_key="abcd1234",
        evidence_images={7: "data:image/jpeg;base64,Zm9v"},  # 不同頁
    ))
    assert "data:image/jpeg;base64,Zm9v" not in html
    # 文字頁碼跳轉仍在
    assert "看標註第 11 頁" in html


def test_same_page_thumbnail_embedded_only_once():
    # 兩條 finding 都指第 11 頁 → 圖只內嵌一次，避免重複塞 base64 撐爆報告
    findings = [
        Finding("CALC-001", "容積獎勵", "fail", "critical", "超上限",
                evidence="審議資料表第 11 頁"),
        Finding("CALC-004", "上限驗算", "fail", "high", "不符",
                evidence="審議資料表第 11 頁"),
    ]
    html = generate_report(_report(
        findings, annotated_pdf_key="abcd1234",
        evidence_images={11: "data:image/jpeg;base64,Zm9v"},
    ))
    assert html.count("data:image/jpeg;base64,Zm9v") == 1
    # 兩條的文字頁碼跳轉都仍在
    assert html.count("看標註第 11 頁") == 2


def test_no_thumbnail_without_evidence_images():
    f = Finding(
        rule_id="CALC-001", rule_name="容積獎勵", status="fail", severity="critical",
        message="超出上限", evidence="審議資料表第 11 頁",
    )
    html = generate_report(_report([f], annotated_pdf_key="abcd1234"))
    assert "data:image" not in html
    assert "看標註第 11 頁" in html


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


# ── 關鍵數字清單 (副總 #2) ──────────────────────────────────────────────────

def test_key_numbers_lists_review_table_values_with_source_page():
    rows = key_numbers(_report([], review_table=_rt(),
                               report_date="112年12月26日",
                               report_date_source="審議資料表（辦理過程報核日）",
                               report_date_page=11))
    by_label = {r["label"]: r for r in rows}
    assert by_label["容積獎勵申請額度"]["value"] == "1,928.58 m²"
    assert by_label["容積獎勵申請額度"]["page"] == 11
    assert by_label["容積獎勵申請額度"]["source"] == "審議資料表"
    assert by_label["法定(含無障礙)汽車停車位"]["value"] == "58 輛"
    assert by_label["報核日期"]["value"] == "112年12月26日"
    assert by_label["報核日期"]["page"] == 11


def test_report_date_single_row_without_secondary():
    rows = key_numbers(_report([], review_table=_rt(), report_date="112年12月26日"))
    labels = [r["label"] for r in rows]
    assert "報核日期" in labels
    assert "事業計畫書 報核日期" not in labels


def test_report_dates_split_when_secondary_present():
    rows = key_numbers(_report(
        [], review_table=_rt(), report_date="112年12月26日",
        report_date_source="審議資料表（辦理過程報核日）", report_date_page=11,
        report_date_secondary="113年5月13日",
        report_date_secondary_source="檔名日期（自動辨識）",
    ))
    by_label = {r["label"]: r for r in rows}
    assert by_label["事業計畫書 報核日期"]["value"] == "112年12月26日"
    assert by_label["權利變換計畫書 報核日期"]["value"] == "113年5月13日"
    assert by_label["權利變換計畫書 報核日期"]["source"] == "檔名日期（自動辨識）"
    # 單一「報核日期」不再出現（已分列）
    assert "報核日期" not in by_label


def test_key_numbers_flags_missing_fields():
    rows = key_numbers(_report([], review_table=_rt(bonus_limit=None)))
    limit = next(r for r in rows if r["label"] == "容積獎勵上限")
    assert limit["missing"] is True
    assert limit["value"] is None


def test_key_numbers_without_review_table_still_lists_report_date():
    rows = key_numbers(_report([], review_table=None))
    labels = [r["label"] for r in rows]
    assert labels == ["報核日期"]
    assert rows[0]["missing"] is True  # no report_date passed


def test_key_numbers_rendered_with_jump_link():
    html = generate_report(_report([], annotated_pdf_key="key99",
                                   review_table=_rt()))
    assert "關鍵數字清單" in html
    assert "/download/key99#page=11" in html


# ── 審核意見結構化輸出 (副總 #6) ─────────────────────────────────────────────

def test_opinion_groups_fail_and_warn_with_details():
    findings = [
        Finding("CALC-001", "容積獎勵", "fail", "critical",
                "申請額超過上限", reference="都更條例第65條",
                expected_calc="基準 × 50% = 上限 1,877.63",
                computed_result="超出 50.95 m²"),
        Finding("FORM-002", "填表日期", "warn", "medium", "日期未識別"),
        Finding("CALC-003", "實設停車", "pass", "critical", "符合"),
    ]
    text = audit_opinion_text(_report(findings, report_date="112年12月26日",
                                      report_date_source="審議資料表",
                                      report_date_page=11))
    assert "一、應修正項目（必檢）" in text
    assert "【CALC-001】容積獎勵" in text
    assert "核算：基準 × 50% = 上限 1,877.63；超出 50.95 m²" in text
    assert "法源：都更條例第65條" in text
    assert "二、待人工核對項目（建議提醒）" in text
    assert "【FORM-002】填表日期" in text
    assert "三、通過項目：共 1 項" in text
    assert "報核日期：112年12月26日" in text


def test_opinion_shows_none_for_empty_sections():
    text = audit_opinion_text(_report([
        Finding("CALC-003", "實設停車", "pass", "critical", "符合"),
    ]))
    # 無 fail、無 warn → 兩節都顯示（無）
    assert text.count("　（無）") == 2
    assert "三、通過項目：共 1 項" in text


def test_opinion_rendered_with_copy_button():
    html = generate_report(_report([
        Finding("CALC-001", "容積獎勵", "fail", "critical", "超過上限"),
    ]))
    assert "複製審核意見" in html
    assert 'id="opinion-text"' in html
