"""格式母版 schema（① 官方 ODT 範本 → 機讀結構）。

臺北市都更處公告的計畫書「範本」是 ODT（OpenDocument Text，本質為 zip+XML），
版面固定。本模組把官方範本解析成一份機讀的 `TemplateSchema`：章節清單、附錄清單、
以及每節的「必附性」（由標題內的「（若無則免附）／（請擇一填寫）」自動判定）。

用途：
- 免人工標註的 ground truth——「正確的文件長什麼樣」。
- 供 `format_checker` 做格式校正（缺章節／缺附錄／擇一未填）。
- 可由 schema 自動產生 playbook 附錄必附規則（規則外部化）。

執行期讀「已產生並簽核」的 JSON（`auditor/templates/schema_<version>_<doc>.json`），
不需要在 production 帶 ODT。`parse_odt()` 僅供離線產生 schema 用（見
`scripts/build_template_schema.py`）。ODT 為官方公開空白範本，不含任何個資。
"""
from __future__ import annotations

import json
import re
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Literal, Optional, Tuple
from xml.etree import ElementTree as ET

Requirement = Literal["required", "choose_one", "optional"]
SectionKind = Literal["chapter", "appendix"]

_ODT_NS = {
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
}


def _q(tag: str) -> str:
    prefix, local = tag.split(":")
    return f"{{{_ODT_NS[prefix]}}}{local}"


# 章節序標（天干式）壹～拾捌；附錄以「附錄」開頭。
_MARKER_RE = re.compile(r"^(附錄[一二三四五六七八九十百]+|[壹貳參肆伍陸柒捌玖拾]+)、")

# 必附性標記（出現於標題括號內）。全形/半形括號都容忍。
_OPTIONAL_MARKERS = ("若無則免附", "視實際情形", "請視實際")
_CHOOSE_ONE_MARKERS = ("請擇一填寫", "擇一填寫", "請擇一")


@dataclass(frozen=True)
class TemplateSection:
    """範本中的一個頂層節（章 or 附錄）。"""

    marker: str            # 壹 / 貳 / ... / 附錄一 / 附錄二 ...
    title: str             # 去掉序標後的節名（如「計畫緣起與目標」）
    kind: SectionKind      # chapter | appendix
    requirement: Requirement
    order: int             # 在文件中的出現序（1-based）


@dataclass(frozen=True)
class TemplateSchema:
    """一份官方範本的機讀結構。"""

    doc_type: str          # 事業計畫書 / 權利變換計畫書 / 事業概要計畫書 ...
    version: str           # 113 / 111 ...
    source: str            # 來源 URL 或說明
    sections: Tuple[TemplateSection, ...]

    # --- 便利查詢 ---
    @property
    def chapters(self) -> Tuple[TemplateSection, ...]:
        return tuple(s for s in self.sections if s.kind == "chapter")

    @property
    def appendices(self) -> Tuple[TemplateSection, ...]:
        return tuple(s for s in self.sections if s.kind == "appendix")

    def required(self, kind: Optional[SectionKind] = None) -> Tuple[TemplateSection, ...]:
        return tuple(
            s for s in self.sections
            if s.requirement == "required" and (kind is None or s.kind == kind)
        )

    def choose_one_groups(self) -> Tuple[Tuple[TemplateSection, ...], ...]:
        """回傳「擇一填寫」的節群。範本以連續的 choose_one 標記為同一群。"""
        groups: List[List[TemplateSection]] = []
        run: List[TemplateSection] = []
        for s in self.sections:
            if s.requirement == "choose_one":
                run.append(s)
            elif run:
                groups.append(run)
                run = []
        if run:
            groups.append(run)
        return tuple(tuple(g) for g in groups)

    # --- 序列化 ---
    def to_dict(self) -> dict:
        return {
            "doc_type": self.doc_type,
            "version": self.version,
            "source": self.source,
            "sections": [asdict(s) for s in self.sections],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TemplateSchema":
        return cls(
            doc_type=d["doc_type"],
            version=str(d["version"]),
            source=d.get("source", ""),
            sections=tuple(TemplateSection(**s) for s in d["sections"]),
        )


def _classify_requirement(title: str) -> Requirement:
    if any(m in title for m in _CHOOSE_ONE_MARKERS):
        return "choose_one"
    if any(m in title for m in _OPTIONAL_MARKERS):
        return "optional"
    return "required"


def _heading_text(h: ET.Element) -> str:
    return "".join(h.itertext()).strip()


def parse_odt(odt_path: str, doc_type: str, version: str, source: str = "") -> TemplateSchema:
    """解析官方 ODT 範本 → TemplateSchema。僅取 outline-level 1 且帶序標的頂層節。

    離線產生用；production 改讀 `load_schema()`。ODT 為公開空白範本、無個資。
    """
    path = Path(odt_path)
    if not path.exists():
        raise FileNotFoundError(f"ODT 範本不存在：{odt_path}")
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("content.xml"))

    sections: List[TemplateSection] = []
    order = 0
    for h in root.iter(_q("text:h")):
        level = h.get(_q("text:outline-level"))
        if level != "1":
            continue
        text = _heading_text(h)
        m = _MARKER_RE.match(text)
        if not m:
            continue
        marker = m.group(1)
        title = text[m.end():].strip()
        kind: SectionKind = "appendix" if marker.startswith("附錄") else "chapter"
        order += 1
        sections.append(TemplateSection(
            marker=marker,
            title=title,
            kind=kind,
            requirement=_classify_requirement(title),
            order=order,
        ))
    if not sections:
        raise ValueError(f"ODT 未解析到任何頂層節（版面非預期？）：{odt_path}")
    return TemplateSchema(doc_type=doc_type, version=version, source=source,
                          sections=tuple(sections))


def templates_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "templates"


def schema_path(doc_type: str, version: str = "113") -> Path:
    return templates_dir() / f"schema_{version}_{doc_type}.json"


def load_schema(doc_type: str, version: str = "113") -> Optional[TemplateSchema]:
    """讀取已簽核的 JSON schema。缺檔回 None（→ 呼叫端略過格式校正、不誤報）。"""
    p = schema_path(doc_type, version)
    if not p.exists():
        return None
    try:
        return TemplateSchema.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def save_schema(schema: TemplateSchema) -> Path:
    templates_dir().mkdir(parents=True, exist_ok=True)
    p = schema_path(schema.doc_type, schema.version)
    p.write_text(
        json.dumps(schema.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return p
