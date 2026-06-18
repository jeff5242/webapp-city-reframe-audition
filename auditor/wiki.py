from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

_WIKI_DIR = Path(__file__).parent.parent / "docs" / "wiki"
_VERSION_YEARS = [107, 108, 111, 113]

_IMPACT_ORDER = {"critical": 0, "major": 1, "medium": 2, "minor": 3}
_CONFIDENCE_LABEL = {
    "high":   ("bg-green-100 text-green-800",  "資料確認"),
    "medium": ("bg-yellow-100 text-yellow-800", "待確認"),
    "low":    ("bg-red-100 text-red-800",       "待補充"),
}
_CHANGE_TYPE_LABEL = {
    "major_rewrite": ("bg-red-100 text-red-800",    "全面修訂"),
    "new":           ("bg-blue-100 text-blue-800",   "新增"),
    "amendment":     ("bg-yellow-100 text-yellow-800", "修訂"),
    "removed":       ("bg-gray-100 text-gray-700",   "刪除"),
}
_FIELD_STATUS_LABEL = {
    "existing":  ("bg-gray-100 text-gray-700", "沿用"),
    "new":       ("bg-blue-100 text-blue-800", "新增"),
    "updated":   ("bg-yellow-100 text-yellow-800", "更新"),
    "not_exist": ("bg-red-100 text-red-700",   "未設欄位"),
    "removed":   ("bg-red-100 text-red-700",   "已刪除"),
}


def _load_version(year: int) -> Optional[Dict[str, Any]]:
    path = _WIKI_DIR / f"{year}.yaml"
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    # Annotate items with display helpers
    for rev in data.get("key_revisions", []):
        rev["impact_order"] = _IMPACT_ORDER.get(rev.get("impact", "minor"), 9)
        rev["type_badge"] = _CHANGE_TYPE_LABEL.get(
            rev.get("type", "amendment"), ("bg-gray-100 text-gray-700", rev.get("type", ""))
        )
        conf = rev.get("data_confidence", data.get("data_confidence", "high"))
        rev["conf_badge"] = _CONFIDENCE_LABEL.get(conf, _CONFIDENCE_LABEL["medium"])
    for field in data.get("form_fields", []):
        field["status_badge"] = _FIELD_STATUS_LABEL.get(
            field.get("status", "existing"), ("bg-gray-100 text-gray-700", field.get("status", ""))
        )
    return data


def _compute_diff(prev: Dict[str, Any], curr: Dict[str, Any]) -> Dict[str, Any]:
    """Produce a structured diff between two consecutive version wikis."""
    prev_fields = {f["name"]: f for f in prev.get("form_fields", [])}
    curr_fields = {f["name"]: f for f in curr.get("form_fields", [])}

    field_changes: List[Dict[str, Any]] = []
    for name, cf in curr_fields.items():
        pf = prev_fields.get(name)
        if pf is None:
            field_changes.append({"name": name, "change": "added", "badge": ("bg-blue-100 text-blue-800", "新增欄位"), "note": cf.get("note", "")})
        elif pf.get("status") != cf.get("status"):
            field_changes.append({"name": name, "change": "updated", "badge": ("bg-yellow-100 text-yellow-800", "欄位更新"),
                                   "from": pf.get("status"), "to": cf.get("status"), "note": cf.get("note", "")})
    for name in prev_fields:
        if name not in curr_fields:
            field_changes.append({"name": name, "change": "removed", "badge": ("bg-red-100 text-red-700", "欄位刪除"), "note": ""})

    # Technical rule changes (simple key comparison)
    rule_changes: List[Dict[str, Any]] = []
    prev_rules = prev.get("technical_rules", {})
    curr_rules = curr.get("technical_rules", {})
    for key in set(list(prev_rules.keys()) + list(curr_rules.keys())):
        pv = prev_rules.get(key, {})
        cv = curr_rules.get(key, {})
        pf_str = pv.get("formula", "") if isinstance(pv, dict) else str(pv)
        cf_str = cv.get("formula", "") if isinstance(cv, dict) else str(cv)
        if pf_str != cf_str and cf_str:
            rule_changes.append({
                "key": key,
                "prev": pf_str,
                "curr": cf_str,
                "note": cv.get("note", "") if isinstance(cv, dict) else "",
                "since": cv.get("since", "") if isinstance(cv, dict) else "",
            })

    # Doc structure changes
    prev_struct = prev.get("doc_structure", {})
    curr_struct = curr.get("doc_structure", {})
    struct_changes: List[str] = []
    if prev_struct.get("appendices") != curr_struct.get("appendices"):
        struct_changes.append(
            f"附錄數量：{prev_struct.get('appendices')} → {curr_struct.get('appendices')}"
        )
    if prev_struct.get("main_chapters") != curr_struct.get("main_chapters"):
        struct_changes.append(
            f"主文章數：{prev_struct.get('main_chapters')} → {curr_struct.get('main_chapters')}"
        )

    # Key revisions (already sorted in the curr wiki)
    key_revisions = sorted(
        curr.get("key_revisions", []),
        key=lambda r: r.get("impact_order", 9),
    )

    return {
        "from_version": prev["version"],
        "to_version": curr["version"],
        "from_year": prev["year"],
        "to_year": curr["year"],
        "from_date": prev["effective_date"],
        "to_date": curr["effective_date"],
        "key_revisions": key_revisions,
        "field_changes": field_changes,
        "rule_changes": rule_changes,
        "struct_changes": struct_changes,
        "revision_summary": curr.get("revision_summary", ""),
    }


def load_all_wiki() -> Dict[str, Any]:
    versions = []
    for year in _VERSION_YEARS:
        data = _load_version(year)
        if data:
            versions.append(data)

    diffs = []
    for i in range(len(versions) - 1):
        diffs.append(_compute_diff(versions[i], versions[i + 1]))

    return {"versions": versions, "diffs": diffs}
