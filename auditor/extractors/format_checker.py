"""格式校正（② 上傳文件結構 vs 官方範本 schema）。

比對上傳文件「有哪些章節／附錄」與官方 `TemplateSchema` 的「該有哪些」，
產出確定性的格式 Findings：缺必附章節、缺必附附錄、擇一群未擇一。

設計要點：
- **確定性、不依賴 VLM**——純比對，100% 可解釋，不受 OCR 準確率拖累。
- 偵測（雜訊來源）與檢核（確定性）分離：`detect_present_sections()` 盡力比對，
  `check_format()` 只吃「已偵測的節集合」做判定。偵測不到（空文字）→ 呼叫端略過，
  絕不誤報缺件（沿用本專案「抓不到就 skip」的守則）。
- 只判「必附」與「擇一」；「若無則免附／視實際情形」一律不報缺件。
"""
from __future__ import annotations

import unicodedata
from typing import List, Set

from ..models import Finding
from .template_schema import TemplateSchema, TemplateSection

# 去括號後取此長度的節名核心當比對關鍵字（避免長標題整串比對易 miss）。
_TITLE_KEY_LEN = 6


def _normalize(text: str) -> str:
    """NFKC + 去空白，容忍全半形與 OCR 常見空白插入。"""
    return unicodedata.normalize("NFKC", text).replace(" ", "").replace("　", "")


def _title_key(section: TemplateSection) -> str:
    """節名核心關鍵字：去掉括號附註後取前 N 字。"""
    title = section.title
    for br in ("（", "("):
        idx = title.find(br)
        if idx > 0:
            title = title[:idx]
            break
    return _normalize(title)[:_TITLE_KEY_LEN]


def detect_present_sections(text: str, schema: TemplateSchema) -> Set[str]:
    """從文件文字（目錄/標題）盡力偵測「出現了哪些節」，回傳 marker 集合。

    純啟發式（節名核心關鍵字比對）。空文字回空集合 → 呼叫端應略過檢核。
    """
    if not text or not text.strip():
        return set()
    norm = _normalize(text)
    present: Set[str] = set()
    for s in schema.sections:
        key = _title_key(s)
        if key and key in norm:
            present.add(s.marker)
    return present


def check_format(present: Set[str], schema: TemplateSchema) -> List[Finding]:
    """比對 present（已偵測節 marker 集合）與 schema，回傳格式 Findings。

    present 為空 → 回空清單（視為未偵測，不判定），避免誤報整份缺件。
    """
    if not present:
        return []

    findings: List[Finding] = []
    ref = f"{schema.version}年版官方範本"

    # --- 必附章節 ---
    for s in schema.required("chapter"):
        if s.marker not in present:
            findings.append(Finding(
                rule_id=f"FMT-CH-{s.order:02d}",
                rule_name=f"必附章節：{s.marker}、{s.title}",
                status="fail", severity="high",
                message=f"未偵測到必附章節「{s.marker}、{s.title}」",
                reference=ref,
            ))

    # --- 必附附錄 ---
    for s in schema.required("appendix"):
        if s.marker not in present:
            findings.append(Finding(
                rule_id=f"FMT-AP-{s.order:02d}",
                rule_name=f"必附附錄：{s.marker}、{s.title}",
                status="fail", severity="high",
                message=f"未偵測到必附附錄「{s.marker}、{s.title}」（{ref}必附）",
                reference=ref,
            ))

    # --- 擇一群：整群皆未出現才報 ---
    for group in schema.choose_one_groups():
        if not any(s.marker in present for s in group):
            names = "、".join(f"{s.marker}" for s in group)
            findings.append(Finding(
                rule_id=f"FMT-XOR-{group[0].order:02d}",
                rule_name=f"擇一填寫：{names}",
                status="fail", severity="medium",
                message=f"擇一填寫群「{names}」未偵測到任一節（應擇一填寫）",
                reference=ref,
            ))

    return findings


def check_document(text: str, schema: TemplateSchema) -> List[Finding]:
    """便利入口：偵測 + 檢核一次做完。文字為空 → 回空清單（不判定）。"""
    return check_format(detect_present_sections(text, schema), schema)
