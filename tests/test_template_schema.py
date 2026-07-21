"""① 格式母版 schema 解析 + ② 格式校正測試。"""
import io
import zipfile

import pytest

from auditor.extractors.template_schema import (
    TemplateSchema,
    TemplateSection,
    parse_odt,
    load_schema,
    _classify_requirement,
)
from auditor.extractors.format_checker import (
    detect_present_sections,
    check_format,
    check_document,
)


# --- 合成最小 ODT（供 parse_odt 覆蓋，不需真範本）---
def _make_odt(path, headings):
    """headings: list of (outline_level, text)。"""
    ns = 'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"'
    hs = "".join(
        f'<text:h text:outline-level="{lv}" {ns}>{t}</text:h>' for lv, t in headings
    )
    content = (
        '<?xml version="1.0"?>'
        f'<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0">'
        f'<office:body>{hs}</office:body></office:document-content>'
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("content.xml", content)


@pytest.fixture
def sample_odt(tmp_path):
    path = tmp_path / "sample.odt"
    _make_odt(path, [
        ("1", "壹、計畫緣起與目標"),
        ("1", "貳、計畫地區範圍"),
        ("1", "柒、整建或維護計畫（請擇一填寫）"),
        ("1", "捌、保存或維護計畫（請擇一填寫）"),
        ("3", "（不該被當成頂層節的子標題）"),
        ("1", "附錄一、實施者證明文件"),
        ("1", "附錄三、事業概要核准函影本（若無則免附）"),
    ])
    return str(path)


class TestClassifyRequirement:
    def test_choose_one(self):
        assert _classify_requirement("整建或維護計畫（請擇一填寫）") == "choose_one"

    def test_optional(self):
        assert _classify_requirement("事業概要核准函影本（若無則免附）") == "optional"

    def test_required_default(self):
        assert _classify_requirement("實施者證明文件") == "required"


class TestParseOdt:
    def test_parses_chapters_and_appendices(self, sample_odt):
        s = parse_odt(sample_odt, "測試計畫書", "113")
        assert len(s.chapters) == 4       # 壹貳柒捌
        assert len(s.appendices) == 2     # 附錄一、附錄三

    def test_skips_non_level1_headings(self, sample_odt):
        s = parse_odt(sample_odt, "測試計畫書", "113")
        titles = [x.title for x in s.sections]
        assert "（不該被當成頂層節的子標題）" not in titles

    def test_requirement_from_markers(self, sample_odt):
        s = parse_odt(sample_odt, "測試計畫書", "113")
        by_marker = {x.marker: x for x in s.sections}
        assert by_marker["柒"].requirement == "choose_one"
        assert by_marker["附錄三"].requirement == "optional"
        assert by_marker["附錄一"].requirement == "required"

    def test_choose_one_group(self, sample_odt):
        s = parse_odt(sample_odt, "測試計畫書", "113")
        groups = s.choose_one_groups()
        assert len(groups) == 1
        assert {x.marker for x in groups[0]} == {"柒", "捌"}

    def test_empty_odt_raises(self, tmp_path):
        p = tmp_path / "empty.odt"
        _make_odt(p, [("2", "只有一個不帶序標的標題")])
        with pytest.raises(ValueError):
            parse_odt(str(p), "x", "113")

    def test_roundtrip_serialization(self, sample_odt):
        s = parse_odt(sample_odt, "測試計畫書", "113")
        assert TemplateSchema.from_dict(s.to_dict()) == s


class TestLoadRealSchema:
    """驗證已提交的 113 年真實 schema 可載入且結構正確。"""

    def test_business_plan_loads(self):
        s = load_schema("事業計畫書", "113")
        assert s is not None
        assert len(s.chapters) == 18
        assert len(s.appendices) == 24

    def test_missing_schema_returns_none(self):
        assert load_schema("不存在的文件", "999") is None


class TestFormatChecker:
    def _schema(self):
        return TemplateSchema(
            doc_type="測試", version="113", source="test",
            sections=(
                TemplateSection("壹", "計畫緣起", "chapter", "required", 1),
                TemplateSection("貳", "計畫範圍", "chapter", "required", 2),
                TemplateSection("柒", "整建計畫", "chapter", "choose_one", 3),
                TemplateSection("捌", "保存計畫", "chapter", "choose_one", 4),
                TemplateSection("附錄一", "實施者證明文件", "appendix", "required", 5),
                TemplateSection("附錄三", "核准函影本", "appendix", "optional", 6),
            ),
        )

    def test_detect_present_from_text(self):
        s = self._schema()
        text = "目錄 壹、計畫緣起 貳、計畫範圍 附錄一、實施者證明文件"
        present = detect_present_sections(text, s)
        assert "壹" in present and "貳" in present and "附錄一" in present

    def test_missing_required_appendix_flagged(self):
        s = self._schema()
        present = {"壹", "貳", "柒"}  # 缺 附錄一
        findings = check_format(present, s)
        ids = {f.rule_id for f in findings}
        assert any(f.rule_name.startswith("必附附錄") for f in findings)
        assert all(f.status == "fail" for f in findings)

    def test_optional_appendix_never_flagged(self):
        s = self._schema()
        present = {"壹", "貳", "柒", "附錄一"}  # 缺 附錄三(選附)
        findings = check_format(present, s)
        assert not any("附錄三" in f.message for f in findings)

    def test_choose_one_satisfied_by_one(self):
        s = self._schema()
        present = {"壹", "貳", "柒", "附錄一"}  # 柒 在，捌 不在 → 擇一已滿足
        findings = check_format(present, s)
        assert not any(f.rule_id.startswith("FMT-XOR") for f in findings)

    def test_choose_one_all_missing_flagged(self):
        s = self._schema()
        present = {"壹", "貳", "附錄一"}  # 柒捌皆缺
        findings = check_format(present, s)
        assert any(f.rule_id.startswith("FMT-XOR") for f in findings)

    def test_complete_document_no_findings(self):
        s = self._schema()
        present = {"壹", "貳", "柒", "附錄一"}
        assert check_format(present, s) == []

    def test_empty_present_skips(self):
        # 偵測不到 → 不判定，絕不誤報整份缺件
        assert check_format(set(), self._schema()) == []

    def test_check_document_empty_text_skips(self):
        assert check_document("", self._schema()) == []
