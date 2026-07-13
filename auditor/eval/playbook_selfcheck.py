"""Playbook 規則自我驗證（gold-case）。

驗證 PlaybookRule 各型別的**判定邏輯**。用 inline 測試規格（強制 enabled），
與正式 playbook 的 enabled 狀態解耦——正式檔可停用重複規則，但邏輯仍須被測到。

執行：python -m auditor.eval.playbook_selfcheck
"""
from __future__ import annotations

import sys

from ..models import AuditData, ReviewTableData
from ..rules.playbook import PlaybookRule

# inline 測試規格（等同 playbook 條目，但一律 enabled 以測邏輯）
SPECS = {
    "PB-TAB-01": {"rule_id": "PB-TAB-01", "rule_name": "含無障礙車位欄位",
                  "type": "review_field_present", "field": "accessible_parking", "severity": "high"},
    "PB-TAB-02": {"rule_id": "PB-TAB-02", "rule_name": "含充電車位欄位",
                  "type": "review_field_present", "field": "ev_parking", "severity": "high"},
    "PB-CALC-01": {"rule_id": "PB-CALC-01", "rule_name": "衍生上限=基準×50%",
                   "type": "formula_check", "target": "bonus_limit", "ref_field": "base_floor_area",
                   "factor": 0.5, "tolerance": 0.01, "severity": "critical"},
    "PB-CALC-02": {"rule_id": "PB-CALC-02", "rule_name": "獎勵樓地板≤上限",
                   "type": "threshold", "field": "bonus_floor_area", "op": "<=",
                   "ref_field": "bonus_limit", "severity": "critical"},
}


def _rt(**kw) -> ReviewTableData:
    base = dict(
        case_name=None, implementer=None, implementer_id=None, submission_type="B-1",
        fill_date=None, land_area=None, base_floor_area=None, bonus_floor_area=None,
        bonus_limit=None, legal_parking=None, actual_parking=None, accessible_parking=None,
        ev_parking=None, owner_consent_ratio=None, raw_page=5,
    )
    base.update(kw)
    return ReviewTableData(**base)


def _audit(rt: ReviewTableData) -> AuditData:
    return AuditData(review_table=rt, front_docs=None, pii_risks=())


CASES = [
    ("合規案：欄位齊、上限=基準×50%、獎勵未逾上限",
     _rt(base_floor_area=1000.0, bonus_limit=500.0, bonus_floor_area=480.0,
         accessible_parking=2, ev_parking=0),
     {"PB-TAB-01": "pass", "PB-TAB-02": "pass", "PB-CALC-01": "pass", "PB-CALC-02": "pass"}),
    ("違規案：上限公式錯、獎勵逾上限、缺無障礙欄位",
     _rt(base_floor_area=1000.0, bonus_limit=600.0, bonus_floor_area=650.0,
         accessible_parking=None, ev_parking=3),
     {"PB-TAB-01": "fail", "PB-TAB-02": "pass", "PB-CALC-01": "fail", "PB-CALC-02": "fail"}),
    ("資料不足案：無數值 → 應 skip 不誤判",
     _rt(),
     {"PB-CALC-01": "skip", "PB-CALC-02": "skip"}),
]


def main() -> int:
    rules = {rid: PlaybookRule(spec) for rid, spec in SPECS.items()}
    print(f"測試 {len(rules)} 條 playbook 規則邏輯\n")
    total = failed = 0
    for desc, rt, expected in CASES:
        print(f"■ {desc}")
        data = _audit(rt)
        for rid, want in expected.items():
            total += 1
            got = rules[rid].evaluate(data)
            ok = got.status == want
            if not ok:
                failed += 1
            extra = f"  ｜三段式: {got.expected_calc}" if got.expected_calc else ""
            print(f"   {'✓' if ok else '✗'} {rid} 期望={want} 實際={got.status}{extra}")
        print()
    print(f"=== 結果：{total - failed}/{total} 通過 ===")
    if failed:
        print("✗ 規則邏輯與預期不符")
        return 1
    print("✓ playbook 規則邏輯正確")
    return 0


if __name__ == "__main__":
    sys.exit(main())
