"""附錄偵測 + 附錄必附規則 自我驗證。

執行：python -m auditor.eval.attachments_selfcheck
"""
from __future__ import annotations

import sys

from ..models import AuditData, ReviewTableData
from ..extractors.attachments import detect_attachments
from ..rules.playbook import PlaybookRule

SAMPLE_TOC = """
附錄一 實施者證明文件
附錄七 更新單元土地權屬清冊
附錄十四 建築工程建材設備等級表
附錄十五 住戶管理規約
附錄二十四 都市更新事業計畫圖
（本案未附 建材設備等級表 以外之其他等級表）
"""  # 注意：故意「缺」都市更新事業計畫圖以外者齊、缺核准函以測 fail


def _audit(attachments):
    rt = ReviewTableData(**{k: None for k in [
        'case_name','implementer','implementer_id','submission_type','fill_date','land_area',
        'base_floor_area','bonus_floor_area','bonus_limit','legal_parking','actual_parking',
        'accessible_parking','ev_parking','owner_consent_ratio','raw_page']})
    return AuditData(review_table=rt, front_docs=None, pii_risks=(), attachments=attachments)


def _rule(attachment):
    return PlaybookRule({"rule_id": "T", "rule_name": f"附錄「{attachment}」必附",
                         "type": "attachment_present", "attachment": attachment, "severity": "high"})


def main() -> int:
    fails = 0

    print("■ detect_attachments 從目錄文字偵測")
    found = detect_attachments(SAMPLE_TOC)
    print(f"   偵測到：{found}")
    ok_detect = "實施者證明文件" in found and "建築工程建材設備等級表" in found
    print(f"   {'✓' if ok_detect else '✗'} 應含 實施者證明文件 + 建材設備等級表")
    fails += 0 if ok_detect else 1

    print("\n■ attachment_present 規則")
    data = _audit(found)
    cases = [
        ("實施者證明文件", "pass"),        # 有 → pass
        ("建築工程建材設備等級表", "pass"),  # 有 → pass
        ("更新單元核准函", "fail"),          # 缺 → fail
    ]
    for att, want in cases:
        got = _rule(att).evaluate(data).status
        ok = got == want
        fails += 0 if ok else 1
        print(f"   {'✓' if ok else '✗'} {att}：期望 {want} 實際 {got}")

    print("\n■ attachments=None（未偵測）→ 應 skip 不誤報")
    got = _rule("實施者證明文件").evaluate(_audit(None)).status
    ok_skip = got == "skip"
    fails += 0 if ok_skip else 1
    print(f"   {'✓' if ok_skip else '✗'} 未偵測 → {got}")

    print(f"\n=== {'✓ 全部通過' if not fails else f'✗ {fails} 項不符'} ===")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
