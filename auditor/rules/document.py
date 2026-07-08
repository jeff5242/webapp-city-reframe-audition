from __future__ import annotations

from typing import Optional

from ..models import AuditData, Finding, FrontDoc
from .engine import Rule


def _locate(fd, doc_type: str) -> Optional[FrontDoc]:
    """回傳該類文件的代表筆：優先真正內容頁（from_toc=False），退而用目錄列出的那筆。"""
    content = next((d for d in fd.docs if d.doc_type == doc_type and not d.from_toc), None)
    return content or next((d for d in fd.docs if d.doc_type == doc_type), None)


def _doc_evidence(doc: Optional[FrontDoc]) -> str:
    """誠實標示來源：內容頁標實際頁；僅在目錄找到者明講「目錄列出、實際頁待確認」，
    避免把目錄頁誤當成文件所在頁。"""
    if doc is None:
        return ""
    if doc.from_toc:
        return f"第 {doc.page} 頁（目錄列出，實際頁面待人工核對）"
    return f"第 {doc.page} 頁"


class ApplicationFormRule(Rule):
    rule_id = "DOC-001"
    rule_name = "申請書存在"
    severity = "critical"
    reference = "111年版注意事項第1條"

    def evaluate(self, data: AuditData) -> Finding:
        fd = data.front_docs
        if fd is None:
            return self._skip("封面文件未能解析")
        if fd.has_application:
            app = _locate(fd, "申請書")
            msg = "申請書已找到（僅目錄列出，實際頁面待人工核對）" if app and app.from_toc \
                else "申請書已找到"
            return self._pass(msg, evidence=_doc_evidence(app))
        return self._fail("未找到申請書（都市更新事業計畫申請書）")


class AffidavitRule(Rule):
    rule_id = "DOC-002"
    rule_name = "切結書存在"
    severity = "critical"
    reference = "111年版注意事項第1條"

    def evaluate(self, data: AuditData) -> Finding:
        fd = data.front_docs
        if fd is None:
            return self._skip("封面文件未能解析")
        if fd.has_affidavit:
            doc = _locate(fd, "切結書")
            msg = "切結書已找到（僅目錄列出，實際頁面待人工核對）" if doc and doc.from_toc \
                else "切結書已找到"
            return self._pass(msg, evidence=_doc_evidence(doc))
        return self._fail("未找到切結書")


class PowerOfAttorneyRule(Rule):
    rule_id = "DOC-003"
    rule_name = "委託書至少1份"
    severity = "high"

    def evaluate(self, data: AuditData) -> Finding:
        fd = data.front_docs
        if fd is None:
            return self._skip("封面文件未能解析")
        count = fd.poa_count
        if count >= 1:
            purposes = [d.purpose or "用途未識別" for d in fd.docs if d.doc_type == "委託書"]
            return self._pass(
                f"找到 {count} 份委託書",
                evidence="、".join(purposes),
            )
        return self._fail("未找到任何委託書")


class ReviewTablePresentRule(Rule):
    rule_id = "DOC-004"
    rule_name = "審議資料表存在"
    severity = "critical"
    reference = "111年版修訂第2點"

    def evaluate(self, data: AuditData) -> Finding:
        fd = data.front_docs
        if fd is None:
            return self._skip("封面文件未能解析")
        if fd.has_review_table:
            doc = _locate(fd, "審議資料表")
            msg = "臺北市都市更新審議資料表已找到（僅目錄列出，實際頁面待人工核對）" \
                if doc and doc.from_toc else "臺北市都市更新審議資料表已找到"
            return self._pass(msg, evidence=_doc_evidence(doc))
        rt = data.review_table
        if rt is not None:
            return self._pass(
                "臺北市都市更新審議資料表已找到",
                evidence=f"第 {rt.raw_page} 頁",
            )
        return self._fail("未找到臺北市都市更新審議資料表")
