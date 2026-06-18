from __future__ import annotations

from ..models import AuditData, Finding
from .engine import Rule


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
            app = next((d for d in fd.docs if d.doc_type == "申請書"), None)
            return self._pass("申請書已找到", evidence=f"第 {app.page} 頁" if app else "")
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
            doc = next((d for d in fd.docs if d.doc_type == "切結書"), None)
            return self._pass("切結書已找到", evidence=f"第 {doc.page} 頁" if doc else "")
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
            doc = next((d for d in fd.docs if d.doc_type == "審議資料表"), None)
            return self._pass(
                "臺北市都市更新審議資料表已找到",
                evidence=f"第 {doc.page} 頁" if doc else "",
            )
        rt = data.review_table
        if rt is not None:
            return self._pass(
                "臺北市都市更新審議資料表已找到",
                evidence=f"第 {rt.raw_page} 頁",
            )
        return self._fail("未找到臺北市都市更新審議資料表")
