"""OCR 驗證循環自我驗證。

情境一：bonus_limit 被 OCR 讀錯（500 → 50，掉一位數，類似真實「1406→406」千分位 bug）。
        約束「上限=基準×50%」抓到不自洽 → 重抽 → 收斂。
情境二：重抽也救不回（reextract 回 None）→ 殘留約束 → 升級人工。

執行：python -m auditor.eval.verify_loop_selfcheck
"""
from __future__ import annotations

import sys

from ..models import ReviewTableData
from ..extractors.verify_loop import verify_review_table


def _rt(**kw) -> ReviewTableData:
    base = dict(
        case_name=None, implementer=None, implementer_id=None, submission_type="B-1",
        fill_date=None, land_area=None, base_floor_area=None, bonus_floor_area=None,
        bonus_limit=None, legal_parking=None, actual_parking=None, accessible_parking=None,
        ev_parking=None, owner_consent_ratio=None, raw_page=5,
    )
    base.update(kw)
    return ReviewTableData(**base)


def main() -> int:
    fails = 0

    # ── 情境一：可自我修正 ──
    print("■ 情境一：bonus_limit 讀錯（50，應為 500）→ 期望自我修正收斂")
    misread = _rt(base_floor_area=1000.0, bonus_limit=50.0, bonus_floor_area=480.0)
    # 模擬「重抽該格」成功讀回正確值（真實系統：高 zoom 重裁 / VLM 重讀）
    truth = {"bonus_limit": 500.0}
    def good_reextract(field, rt):
        return truth.get(field)
    fixed, log, residual = verify_review_table(misread, reextract=good_reextract)
    for line in log:
        print("   " + line)
    ok1 = (fixed.bonus_limit == 500.0 and not residual)
    print(f"   → 結果：bonus_limit={fixed.bonus_limit}，殘留={residual}  {'✅' if ok1 else '✗'}\n")
    fails += 0 if ok1 else 1

    # ── 情境二：無法修正 → 升級人工 ──
    print("■ 情境二：重抽也救不回 → 期望升級人工（殘留非空）")
    misread2 = _rt(base_floor_area=1000.0, bonus_limit=50.0, bonus_floor_area=480.0)
    def bad_reextract(field, rt):
        return None  # 模擬難頁：怎麼重抽都讀不準
    fixed2, log2, residual2 = verify_review_table(misread2, reextract=bad_reextract)
    for line in log2:
        print("   " + line)
    ok2 = bool(residual2)  # 應有殘留 → 升級人工
    print(f"   → 結果：殘留={residual2} → {'升級人工 ✅' if ok2 else '✗ 未偵測'}\n")
    fails += 0 if ok2 else 1

    # ── 情境三：本來就自洽 → 零疊代 ──
    print("■ 情境三：資料自洽 → 期望零修正")
    clean = _rt(base_floor_area=1000.0, bonus_limit=500.0, bonus_floor_area=480.0)
    fixed3, log3, residual3 = verify_review_table(clean, reextract=good_reextract)
    ok3 = (not residual3 and fixed3 == clean)
    print(f"   → 殘留={residual3}，未更動={fixed3 == clean}  {'✅' if ok3 else '✗'}\n")
    fails += 0 if ok3 else 1

    print(f"=== {3 - fails}/3 情境通過 ===")
    if fails:
        print("✗ 驗證循環行為與預期不符")
        return 1
    print("✓ 驗證循環正確：能自我修正收斂、無法修時升級人工、自洽時不亂動")
    return 0


if __name__ == "__main__":
    sys.exit(main())
