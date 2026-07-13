"""Playbook 規則自我驗證（gold-case）。

用已知輸入的合成審議資料表，跑 playbook 規則，斷言每條規則的 status 符合預期。
這就是「AI 生成規則 → 自動驗證 → 才上線」的守門：改壞了會被抓到。

執行：python -m auditor.eval.playbook_selfcheck
"""
from __future__ import annotations

import sys

from ..models import AuditData, ReviewTableData
from ..rules.playbook import load_playbook, default_playbook_path


def _rt(**overrides) -> ReviewTableData:
    """建一筆審議資料表，未指定欄位填 None。"""
    base = dict(
        case_name=None, implementer=None, implementer_id=None, submission_type="B-1",
        fill_date=None, land_area=None, base_floor_area=None, bonus_floor_area=None,
        bonus_limit=None, legal_parking=None, actual_parking=None, accessible_parking=None,
        ev_parking=None, owner_consent_ratio=None, raw_page=5,
    )
    base.update(overrides)
    return ReviewTableData(**base)


def _audit(rt: ReviewTableData) -> AuditData:
    return AuditData(review_table=rt, front_docs=None, pii_risks=())


# gold cases：(說明, 審議資料表, {rule_id: 預期status})
CASES = [
    (
        "合規案：欄位齊、上限=基準×50%、獎勵未逾上限",
        _rt(base_floor_area=1000.0, bonus_limit=500.0, bonus_floor_area=480.0,
            accessible_parking=2, ev_parking=0),
        {"PB-TAB-01": "pass", "PB-TAB-02": "pass", "PB-CALC-01": "pass", "PB-CALC-02": "pass"},
    ),
    (
        "違規案：上限公式錯（600≠500）、獎勵逾上限、缺無障礙欄位",
        _rt(base_floor_area=1000.0, bonus_limit=600.0, bonus_floor_area=650.0,
            accessible_parking=None, ev_parking=3),
        {"PB-TAB-01": "fail", "PB-TAB-02": "pass", "PB-CALC-01": "fail", "PB-CALC-02": "fail"},
    ),
    (
        "資料不足案：無數值 → 應 skip 不誤判",
        _rt(),
        {"PB-CALC-01": "skip", "PB-CALC-02": "skip"},
    ),
]


def main() -> int:
    path = default_playbook_path("111")
    if not path:
        print("✗ 找不到 playbook_111.json")
        return 2
    rules = {r.rule_id: r for r in load_playbook(path)}
    print(f"載入 playbook：{len(rules)} 條規則\n")

    total = 0
    failed = 0
    for desc, rt, expected in CASES:
        print(f"■ {desc}")
        data = _audit(rt)
        for rid, want in expected.items():
            total += 1
            got = rules[rid].evaluate(data)
            ok = got.status == want
            mark = "✓" if ok else "✗"
            if not ok:
                failed += 1
            extra = f"  ｜三段式: {got.expected_calc}" if got.expected_calc else ""
            print(f"   {mark} {rid} 期望={want} 實際={got.status}{extra}")
        print()

    print(f"=== 結果：{total - failed}/{total} 通過 ===")
    if failed:
        print("✗ 有規則行為與預期不符（改壞了會在這裡被擋下）")
        return 1
    print("✓ 全部符合：playbook 生成的規則行為正確，可上線")
    return 0


if __name__ == "__main__":
    sys.exit(main())
