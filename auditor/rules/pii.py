from __future__ import annotations

from ..models import AuditData, Finding
from .engine import Rule


class HighRiskPiiRule(Rule):
    rule_id = "PII-001"
    rule_name = "高風險個資遮蔽確認"
    severity = "high"
    reference = "個人資料保護法第5條"

    def evaluate(self, data: AuditData) -> Finding:
        high_risk = [r for r in data.pii_risks if r.severity == "HIGH"]
        medium_risk = [r for r in data.pii_risks if r.severity == "MEDIUM"]

        if not data.pii_risks:
            return self._pass("封面文件未偵測到個資風險")

        if high_risk:
            items = "; ".join(
                f"P.{r.page} {r.risk_type}：{r.value}" for r in high_risk[:5]
            )
            return self._fail(
                f"偵測到 {len(high_risk)} 處高風險個資（住宅地址/身分證號），"
                f"上傳雲端版本必須遮蔽",
                evidence=items,
            )

        if medium_risk:
            items = "; ".join(
                f"P.{r.page} {r.risk_type}：{r.value}" for r in medium_risk[:5]
            )
            return self._warn(
                f"偵測到 {len(medium_risk)} 處中風險個資（電話號碼），"
                f"建議確認上傳版本是否需要遮蔽",
                evidence=items,
            )

        return self._pass("未偵測到高風險個資")
