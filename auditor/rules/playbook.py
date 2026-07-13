"""Playbook-driven rules (規則外部化 / rules-as-config)。

從 JSON playbook 讀取「宣告式規則」，轉成可被既有 RuleEngine 執行的 Rule 物件。
新增/修訂規則只需改 playbook JSON，不必寫 Python 類別——對應提升方案「缺口 3：規則外部化」。

安全性：本載入器**不使用 eval/exec**。所有運算以「具名欄位 + 固定運算子/係數」宣告式
表達（避免把設定字串當程式碼執行；參 IFDCS `new Function` RCE 教訓）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Dict, List, Optional

from ..models import AuditData, Finding
from .engine import Rule

# 允許的比較運算子（白名單）
_OPS: Dict[str, Callable[[float, float], bool]] = {
    "<=": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    ">": lambda a, b: a > b,
    "==": lambda a, b: a == b,
}


def _rt_get(data: AuditData, field: str):
    rt = data.review_table
    return getattr(rt, field, None) if rt is not None else None


class PlaybookRule(Rule):
    """由一筆 playbook spec 驅動的通用規則。"""

    def __init__(self, spec: dict):
        self.spec = spec
        self.rule_id = spec["rule_id"]
        self.rule_name = spec["rule_name"]
        self.severity = spec.get("severity", "medium")
        self.reference = spec.get("reference", "")
        self.rule_type = spec["type"]

    def evaluate(self, data: AuditData) -> Finding:
        # 草稿閘門：抽取產生但尚未經承辦複核 / 尚缺 extractor 支援的規則，
        # 標 enabled=false → 只 skip 不上線判定，避免「AI 生成未驗證就影響審議」。
        if not self.spec.get("enabled", True):
            return self._skip(f"草稿規則（待承辦複核／extractor 支援）：{self.spec.get('source_clause', '')}")
        handler = {
            "review_field_present": self._eval_field_present,
            "formula_check": self._eval_formula,
            "threshold": self._eval_threshold,
            "attachment_present": self._eval_attachment,
        }.get(self.rule_type)
        if handler is None:
            # 未實作的型別（如 附錄偵測）→ 明確 skip，不假裝有跑
            return self._skip(f"規則型別「{self.rule_type}」尚未支援（待 extractor/實作）")
        return handler(data)

    # --- 欄位存在（例：審議資料表新增欄位）---
    def _eval_field_present(self, data: AuditData) -> Finding:
        if data.review_table is None:
            return self._skip("審議資料表未解析")
        val = _rt_get(data, self.spec["field"])
        if val is not None:
            return self._pass(f"{self.rule_name}：已具備（值 = {val}）")
        return self._fail(f"{self.rule_name}：缺欄位或未讀到")

    # --- 公式驗算（三段式：申報 / 應為 / 核算）---
    def _eval_formula(self, data: AuditData) -> Finding:
        if data.review_table is None:
            return self._skip("審議資料表未解析")
        target = _rt_get(data, self.spec["target"])
        base = _rt_get(data, self.spec["ref_field"])
        factor = float(self.spec["factor"])
        tol = float(self.spec.get("tolerance", 0.01))
        if target is None or base is None:
            return self._skip(f"缺數值：{self.spec['target']} 或 {self.spec['ref_field']}")
        expected = base * factor
        ok = abs(target - expected) <= max(tol, abs(expected) * tol)
        applied = f"{self.spec['target']} 申報 {target:g}"
        calc = f"{self.spec['ref_field']}({base:g}) × {factor:g} = {expected:g}"
        if ok:
            return self._pass(f"{self.rule_name}：相符", applied_value=applied,
                              expected_calc=calc, computed_result=f"{expected:g}")
        return self._fail(f"{self.rule_name}：不符（申報 {target:g}，應為 {expected:g}）",
                          applied_value=applied, expected_calc=calc, computed_result=f"{expected:g}")

    # --- 門檻比較（可比常數或另一欄位）---
    def _eval_threshold(self, data: AuditData) -> Finding:
        if data.review_table is None:
            return self._skip("審議資料表未解析")
        val = _rt_get(data, self.spec["field"])
        if val is None:
            return self._skip(f"缺數值：{self.spec['field']}")
        op = self.spec["op"]
        cmp = _OPS.get(op)
        if cmp is None:
            return self._skip(f"未支援運算子：{op}")
        if "ref_field" in self.spec:
            ref = _rt_get(data, self.spec["ref_field"])
            if ref is None:
                return self._skip(f"缺對照值：{self.spec['ref_field']}")
            ref_label = self.spec["ref_field"]
        else:
            ref = float(self.spec["value"])
            ref_label = str(ref)
        applied = f"{self.spec['field']} = {val:g}"
        calc = f"需 {op} {ref_label}({ref:g})"
        if cmp(val, ref):
            return self._pass(f"{self.rule_name}：符合", applied_value=applied,
                              expected_calc=calc, computed_result=f"{ref:g}")
        return self._fail(f"{self.rule_name}：不符（{val:g} 未 {op} {ref:g}）",
                          applied_value=applied, expected_calc=calc, computed_result=f"{ref:g}")

    # --- 附錄必附（例：附錄十四 建材設備等級表）---
    def _eval_attachment(self, data: AuditData) -> Finding:
        # 未偵測附錄清單 → skip（不誤報缺件）
        if getattr(data, "attachments", None) is None:
            return self._skip("附錄清單未偵測（extractor 未回傳）")
        name = self.spec["attachment"]
        if name in data.attachments:
            return self._pass(f"{self.rule_name}：已檢附")
        return self._fail(f"{self.rule_name}：未檢附（111年版必附）")


def load_playbook(path: str) -> List[PlaybookRule]:
    """讀取 playbook JSON，回傳 PlaybookRule 清單。"""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    return [PlaybookRule(spec) for spec in doc.get("rules", [])]


def default_playbook_path(version: str = "111") -> Optional[str]:
    p = Path(__file__).resolve().parent.parent / "playbooks" / f"playbook_{version}.json"
    return str(p) if p.exists() else None
