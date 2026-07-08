"""檢測方法說明資料（上傳頁點擊展開）。"""
from __future__ import annotations

from auditor.extractors.term_checker import WRONG_TERMS
from auditor.reporters.method_docs import METHOD_DOCS


def test_all_categories_have_required_fields():
    for d in METHOD_DOCS:
        for key in ("key", "title", "count", "rules", "what", "method", "tech", "docs", "refs"):
            assert key in d, f"{d.get('title')} missing {key}"
        assert d["rules"], f"{d['title']} has no rules"
        for field in ("what", "method", "tech", "docs"):
            assert d[field].strip(), f"{d['title']}.{field} empty"


def test_covers_expected_categories():
    keys = {d["key"] for d in METHOD_DOCS}
    assert {"doc", "form", "calc", "term", "pii", "cons", "cross"} <= keys


def test_document_completeness_covers_four_doc_rules():
    doc = next(d for d in METHOD_DOCS if d["key"] == "doc")
    ids = {r["id"] for r in doc["rules"]}
    assert ids == {"DOC-001", "DOC-002", "DOC-003", "DOC-004"}


def test_calc_covers_four_rules_with_formulas():
    calc = next(d for d in METHOD_DOCS if d["key"] == "calc")
    ids = {r["id"] for r in calc["rules"]}
    assert ids == {"CALC-001", "CALC-002", "CALC-003", "CALC-004"}
    assert calc["extra"]["type"] == "formulas"
    assert len(calc["extra"]["rows"]) == 4


def test_form_covers_three_review_table_rules():
    form = next(d for d in METHOD_DOCS if d["key"] == "form")
    ids = {r["id"] for r in form["rules"]}
    assert ids == {"FORM-001", "FORM-002", "FORM-003"}


def test_term_list_stays_in_sync_with_source():
    # DRY 保證：說明的 15 個詞直接來自 WRONG_TERMS，不會漂移
    term = next(d for d in METHOD_DOCS if d["key"] == "term")
    assert term["extra"]["type"] == "terms"
    pairs = {(t["wrong"], t["correct"]) for t in term["extra"]["rows"]}
    assert pairs == set(WRONG_TERMS.items())
    assert str(len(WRONG_TERMS)) in term["count"]


def test_cross_doc_boundaries_present():
    cross = next(d for d in METHOD_DOCS if d["key"] == "cross")
    assert cross["extra"]["type"] == "boundaries"
    fields = {it["field"] for it in cross["extra"]["rows"]}
    assert "更新單元總面積" in fields
    assert "土地所有權人數" in fields


def test_refs_are_law_or_wiki_links():
    for d in METHOD_DOCS:
        for ref in d["refs"]:
            assert ref["url"].startswith(("http", "/")), f"{d['title']} bad ref url"
            assert ref["label"].strip()
