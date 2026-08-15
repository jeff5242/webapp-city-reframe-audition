from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

from jinja2 import Environment, FileSystemLoader

from ..models import AuditReport, Finding

_TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"

_STATUS_ORDER = {"fail": 0, "warn": 1, "skip": 2, "pass": 3}
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

# 與 annotator._page_from_evidence 同一組 regex：從「…第 N 頁」抽頁碼，
# 供報告產生頁碼跳轉連結（副總 #5 highlight 定位）。
_EVIDENCE_PAGE_RE = re.compile(r"第\s*(\d+)\s*頁")


def _sort_findings(findings: List[Finding]) -> List[Finding]:
    return sorted(
        findings,
        key=lambda f: (
            _STATUS_ORDER.get(f.status, 9),
            _SEVERITY_ORDER.get(f.severity, 9),
        ),
    )


# 檢查項目歸類（理事長 115.08.12 文件·第一階段核心功能）：
# 形式齊備性檢核 / 內容一致性比對＋算式驗算 / 會辦局處研判（規劃中）/ 其他。
# 依 rule_id 前綴歸組；未知前綴落入「其他」。
_RULE_GROUPS = [
    {
        "key": "completeness",
        "title": "一、形式齊備性檢核",
        "note": "法定文件與必填欄位是否齊備（缺漏僅標示提醒，由承辦認定）",
        "prefixes": ("DOC-", "FORM-", "FMT-"),
    },
    {
        "key": "calculation",
        "title": "二、內容一致性比對與算式驗算",
        "note": "跨文件關鍵數值一致性、法規門檻驗算（附計算式）",
        "prefixes": ("CALC-", "CONS-"),
    },
    {
        "key": "referral",
        "title": "三、會辦局處研判與提示",
        "note": "依基地條件研判應會辦之目的事業主管機關",
        "prefixes": (),
        "placeholder": "本階段（pre-POC）尚未涵蓋；規劃於正式 POC 建置。",
    },
    {
        "key": "other",
        "title": "四、其他檢視項目",
        "note": "個資遮蔽確認與法定用詞比對",
        "prefixes": ("PII-", "TERM-"),
    },
]

# 各檢查項目白話說明（報告內「?」展開用，取代翻查上傳頁長清單）。
# FMT- 前綴由 _help_for() 以 startswith 對應。
_RULE_HELP = {
    "DOC-001": "檢視送件 PDF 是否含「申請書」頁（依關鍵字與目錄定位）。未尋得時標示提醒，請承辦確認是否確實缺件。",
    "DOC-002": "檢視是否含實施者「切結書」頁。",
    "DOC-003": "檢視是否含「委託書」（委任代理送件時之必要文件）。",
    "DOC-004": "檢視是否含「審議資料表」——後續欄位核對與算式驗算之資料來源。",
    "FORM-001": "檢視審議資料表「送審類別」欄是否已勾選。",
    "FORM-002": "檢視審議資料表「填表日期」是否填寫（適用法規版次判斷之參考來源之一）。",
    "FORM-003": "111 年版新增「充電車位」欄位，檢視是否填列。",
    "CALC-001": "依都更條例第 65 條驗算：容積獎勵申請額度是否超過上限（基準容積 × 50%），報告附完整計算式。",
    "CALC-002": "依建築技術規則第 167 條之六驗算：無障礙停車位是否達法定最低數量。",
    "CALC-003": "驗算實設汽車停車位是否不低於法定要求。",
    "CALC-004": "驗算審議資料表表列之容積獎勵上限，與「基準容積 × 50%」計算值是否一致。",
    "CONS-001": "比對同一關鍵數值（面積、容積、停車位數等）在文件各處出現時是否前後一致；不一致時僅標示位置，不判斷何者為正確。",
    "TERM-001": "比對法定用詞是否誤用（如「台北市」應為「臺北市」、「權利變換」誤植等）。",
    "PII-001": "偵測文件前段是否有未遮蔽之高風險個資（身分證字號、出生日期等），提醒依個資保護規範處理。",
    "FMT-CH": "依官方範本機讀母版，檢視法定章節是否齊備（確定性比對，不使用 AI）。",
    "FMT-AP": "依官方範本機讀母版，檢視法定附錄是否齊備（確定性比對，不使用 AI）。",
    "FMT-XOR": "範本中「請擇一填寫」之項目，檢視是否至少擇一填列。",
}


def _help_for(rule_id: str) -> Optional[str]:
    """依 rule_id 取得白話說明；FMT-CH-03 等格式母版項目以前綴對應。"""
    if rule_id in _RULE_HELP:
        return _RULE_HELP[rule_id]
    for prefix, text in _RULE_HELP.items():
        if rule_id.startswith(prefix):
            return text
    return None


def group_findings(findings: List[Finding]) -> List[dict]:
    """把檢查結果依理事長第一階段核心功能歸組，供報告分節呈現。

    回傳 [{key,title,note,findings,placeholder?}, ...]；「會辦局處研判」
    目前無對應規則，以 placeholder 呈現 roadmap；空的其他組別直接省略。
    """
    grouped: List[dict] = []
    matched_ids: set = set()
    for spec in _RULE_GROUPS:
        items = [
            f for f in _sort_findings(findings)
            if any(f.rule_id.startswith(p) for p in spec["prefixes"])
        ]
        matched_ids.update(f.rule_id for f in items)
        grouped.append({**spec, "findings": items})

    leftovers = [f for f in _sort_findings(findings) if f.rule_id not in matched_ids]
    if leftovers:
        grouped[-1] = {**grouped[-1], "findings": grouped[-1]["findings"] + leftovers}

    return [g for g in grouped if g["findings"] or g.get("placeholder")]


def _evidence_page(evidence: Optional[str]) -> Optional[int]:
    """從 evidence 字串（如「審議資料表第 11 頁」）抽出頁碼，無則回 None。"""
    if not evidence:
        return None
    m = _EVIDENCE_PAGE_RE.search(evidence)
    return int(m.group(1)) if m else None


def _fmt_area(v: Optional[float]) -> Optional[str]:
    return f"{v:,.2f} m²" if v is not None else None


def _fmt_count(v: Optional[int], unit: str = "輛") -> Optional[str]:
    return f"{v} {unit}" if v is not None else None


def key_numbers(report: AuditReport) -> List[dict]:
    """關鍵數字清單（副總 #2）：把驅動規則的數值集中，標明「系統從哪一頁抓的」。

    純地端模式無 API key，數值來自 Track A 擷取（審議資料表 / 報核日期來源），
    非 LLM。每筆帶 label / value / source / page，供報告顯示與頁碼跳轉。
    缺漏的關鍵欄位仍列出並標記，讓審核者知道哪些要人工補核。
    """
    rt = report.review_table
    page = rt.raw_page if rt else None
    rows: List[dict] = []

    # 報核日期（來源可能是審議資料表/申請書…，頁碼獨立）。兩份文件的報核日可能
    # 不同天，故有次文件時分列「事業計畫書」與「權利變換計畫書」兩筆。
    has_secondary = report.report_date_secondary is not None
    primary_label = "事業計畫書 報核日期" if has_secondary else "報核日期"
    rows.append({
        "label": primary_label,
        "value": report.report_date,
        "source": report.report_date_source or "—",
        "page": report.report_date_page,
        "missing": report.report_date is None,
    })
    if has_secondary:
        rows.append({
            "label": "權利變換計畫書 報核日期",
            "value": report.report_date_secondary,
            "source": report.report_date_secondary_source or "—",
            "page": None,
            "missing": report.report_date_secondary is None,
        })

    if rt is not None:
        # 實設汽車停車位：值後附分項明細（平面/機械/無障礙/充電），對齊審議資料表用字。
        actual_val = _fmt_count(rt.actual_parking)
        if actual_val and rt.actual_parking_detail:
            actual_val = f"{actual_val}（{rt.actual_parking_detail}）"
        # 容積獎勵上限：表列值優先；無表列值但有基準容積時，依都更條例§65 推算=基準×50%。
        limit_val = _fmt_area(rt.bonus_limit)
        limit_missing = rt.bonus_limit is None
        if limit_missing and rt.base_floor_area is not None:
            limit_val = f"{rt.base_floor_area * 0.5:,.2f} m²（依基準×50%推算）"
            limit_missing = False
        specs = [
            ("基準容積", _fmt_area(rt.base_floor_area), rt.base_floor_area is None),
            ("容積獎勵申請額度", _fmt_area(rt.bonus_floor_area), rt.bonus_floor_area is None),
            ("容積獎勵上限", limit_val, limit_missing),
            ("法定(含無障礙)汽車停車位", _fmt_count(rt.legal_parking), rt.legal_parking is None),
            ("實設汽車停車位", actual_val, rt.actual_parking is None),
            ("無障礙停車位", _fmt_count(rt.accessible_parking), rt.accessible_parking is None),
            ("充電車位", _fmt_count(rt.ev_parking), rt.ev_parking is None),
        ]
        for label, value, missing in specs:
            rows.append({
                "label": label,
                "value": value,
                "source": "審議資料表",
                "page": page,
                "missing": missing,
            })

    return rows


_ACTION_HEADINGS = [
    ("fail", "一、應修正項目（必檢）", "請實施者修正後再送。"),
    ("warn", "二、待人工核對項目（建議提醒）", "系統資料不足或需判讀，請承辦人工確認（非實施者違規）。"),
]


def audit_opinion_text(report: AuditReport) -> str:
    """審核意見結構化輸出（副總 #6）：通用條列式純文字，供承辦一鍵複製貼進意見書。

    格式：抬頭（案名/報核日期/適用版次/審查時間）+ 應修正項目 + 待人工核對項目
    + 通過項目統計。每項含 現況／核算／法源／建議，方便人工彙整。
    """
    lines: List[str] = []
    lines.append("都更報核計畫書 收件端前處理整理清單")
    lines.append(f"案名：{report.case_name}")
    if report.report_date:
        src = report.report_date_source or ""
        pg = f" 第{report.report_date_page}頁" if report.report_date_page else ""
        lines.append(f"報核日期：{report.report_date}（來源：{src}{pg}）→ 適用 {report.rule_version}")
    else:
        lines.append(f"適用版次：{report.rule_version}")
    lines.append(f"審查時間：{report.audit_time}")
    lines.append("")

    for status, heading, default_suggestion in _ACTION_HEADINGS:
        items = [f for f in _sort_findings(report.findings) if f.status == status]
        lines.append(heading)
        if not items:
            lines.append("　（無）")
        for idx, f in enumerate(items, 1):
            lines.append(f"{idx}. 【{f.rule_id}】{f.rule_name}")
            lines.append(f"　現況：{f.message}")
            if f.expected_calc or f.computed_result:
                calc = "；".join(x for x in (f.expected_calc, f.computed_result) if x)
                lines.append(f"　核算：{calc}")
            if f.reference:
                lines.append(f"　法源：{f.reference}")
            lines.append(f"　建議：{default_suggestion}")
        lines.append("")

    passes = [f for f in report.findings if f.status == "pass"]
    lines.append(
        f"三、未發現缺漏之項目：共 {len(passes)} 項"
        "（逐項比對完成、未發現缺漏；本清單僅供參考，仍以承辦認定為準）。"
    )
    return "\n".join(lines)


def generate_report(report: AuditReport, templates_dir: Optional[str] = None) -> str:
    tdir = templates_dir or str(_TEMPLATES_DIR)
    env = Environment(loader=FileSystemLoader(tdir), autoescape=True)
    env.filters["evidence_page"] = _evidence_page
    template = env.get_template("report.html")
    return template.render(
        report=report,
        sorted_findings=_sort_findings(report.findings),
        grouped_findings=group_findings(report.findings),
        rule_help={f.rule_id: _help_for(f.rule_id) for f in report.findings},
        key_numbers=key_numbers(report),
        audit_opinion_text=audit_opinion_text(report),
    )
