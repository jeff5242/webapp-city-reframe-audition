"""Tests for the hybrid review-table extractor (on-prem structure + vision)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from auditor.models import ReviewTableData
from auditor.extractors import table_extractor as te


def _base(**overrides) -> ReviewTableData:
    defaults = dict(
        case_name=None, implementer=None, implementer_id=None,
        submission_type=None, fill_date=None, land_area=None,
        base_floor_area=None, bonus_floor_area=None, bonus_limit=None,
        legal_parking=None, actual_parking=None, accessible_parking=None,
        ev_parking=None, owner_consent_ratio=None, raw_page=17,
    )
    defaults.update(overrides)
    return ReviewTableData(**defaults)


# ── numeric coercion ──────────────────────────────────────────────────────────

def test_to_float_strips_units_and_commas():
    assert te._to_float("1,052.00 m²") == 1052.0
    assert te._to_float("3,930.25") == 3930.25
    assert te._to_float("41.73%") == 41.73


def test_to_int_extracts_digits():
    assert te._to_int("42輛") == 42
    assert te._to_int("47 輛") == 47


def test_coerce_by_field_type():
    assert te._coerce("land_area", "1,052.00 m²") == 1052.0
    assert te._coerce("legal_parking", "42輛") == 42
    assert te._coerce("implementer", " 世座建設股份有限公司 ") == "世座建設股份有限公司"


# ── coverage (pure) ───────────────────────────────────────────────────────────

def test_coverage_all_present():
    fields = {k: 1 for k in te._CRITICAL_FIELDS}
    assert te._coverage(fields) == 1.0


def test_coverage_partial():
    fields = {"bonus_floor_area": 100, "legal_parking": 42}
    # 2 of 5 critical fields
    assert te._coverage(fields) == 2 / 5


def test_coverage_none():
    assert te._coverage({}) == 0.0


# ── merge (pure, gap-fill only) ───────────────────────────────────────────────

def test_merge_fills_missing_fields():
    base = _base()
    merged = te._merge_into(base, {"land_area": "1,052.00", "legal_parking": "42輛"})
    assert merged.land_area == 1052.0
    assert merged.legal_parking == 42


def test_merge_never_overwrites_existing():
    base = _base(legal_parking=99)
    merged = te._merge_into(base, {"legal_parking": "42輛"})
    assert merged.legal_parking == 99  # text pass wins


def test_merge_ignores_unknown_fields():
    base = _base()
    merged = te._merge_into(base, {"nonexistent_field": "x"})
    assert merged == base


def test_merge_returns_same_object_when_no_updates():
    base = _base(land_area=1052.0)
    merged = te._merge_into(base, {"land_area": "999"})
    assert merged is base  # nothing to fill → identity


# ── PP-Structure cell mapping (pure) ──────────────────────────────────────────

def test_map_cells_label_to_adjacent_value():
    cells = [
        {"row": 0, "col": 0, "text": "法定汽車停車位"},
        {"row": 0, "col": 1, "text": "42輛"},
        {"row": 0, "col": 2, "text": "實設汽車停車位"},
        {"row": 0, "col": 3, "text": "47輛"},
    ]
    mapped = te._map_structured_cells(cells)
    assert mapped["legal_parking"] == "42輛"
    assert mapped["actual_parking"] == "47輛"


def test_map_cells_specific_label_beats_generic():
    """實設汽車停車位 must map to actual_parking, not be swallowed by 法定."""
    cells = [
        {"row": 1, "col": 0, "text": "基地面積"},
        {"row": 1, "col": 1, "text": "1,052.00 m²"},
    ]
    mapped = te._map_structured_cells(cells)
    assert mapped["land_area"] == "1,052.00 m²"


def test_html_table_to_cells_parses_grid():
    html = "<table><tr><td>法定汽車停車位</td><td>42輛</td></tr></table>"
    cells = te._html_table_to_cells(html)
    assert {"row": 0, "col": 0, "text": "法定汽車停車位"} in cells
    assert {"row": 0, "col": 1, "text": "42輛"} in cells


# ── vision parsing (pure) ─────────────────────────────────────────────────────

def test_parse_vision_fields_drops_nulls():
    raw = {"land_area": 1052.0, "bonus_limit": None, "legal_parking": 42}
    parsed = te._parse_vision_fields(raw)
    assert parsed == {"land_area": 1052.0, "legal_parking": 42}


def test_parse_vision_fields_handles_non_dict():
    assert te._parse_vision_fields(None) == {}


# ── vision extraction (mocked anthropic) ──────────────────────────────────────

def test_extract_via_vision_returns_empty_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert te._extract_via_vision("x.pdf", 17) == {}


def test_extract_via_vision_parses_tool_use(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = "extract_review_table"
    tool_block.input = {"land_area": 1052.0, "legal_parking": 42, "bonus_limit": None}
    response = MagicMock()
    response.content = [tool_block]
    client = MagicMock()
    client.messages.create.return_value = response
    anthropic_mod = MagicMock()
    anthropic_mod.Anthropic.return_value = client

    with patch.dict("sys.modules", {"anthropic": anthropic_mod}), \
         patch.object(te, "_render_page_png", return_value=b"PNGDATA"):
        result = te._extract_via_vision("x.pdf", 17)

    assert result == {"land_area": 1052.0, "legal_parking": 42}


def test_extract_via_vision_sends_image_block(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = "extract_review_table"
    tool_block.input = {}
    response = MagicMock()
    response.content = [tool_block]
    client = MagicMock()
    client.messages.create.return_value = response
    anthropic_mod = MagicMock()
    anthropic_mod.Anthropic.return_value = client

    with patch.dict("sys.modules", {"anthropic": anthropic_mod}), \
         patch.object(te, "_render_page_png", return_value=b"PNGDATA"):
        te._extract_via_vision("x.pdf", 17)

    _, kwargs = client.messages.create.call_args
    content = kwargs["messages"][0]["content"]
    assert content[0]["type"] == "image"
    assert content[0]["source"]["media_type"] == "image/png"


# ── orchestration ─────────────────────────────────────────────────────────────

def test_ppstructure_disabled_by_default(monkeypatch):
    """PP-Structure must return {} unless ENABLE_PPSTRUCTURE is set."""
    monkeypatch.delenv("ENABLE_PPSTRUCTURE", raising=False)
    assert te._extract_via_ppstructure("x.pdf", 10) == {}


def test_ppstructure_gate_reads_env(monkeypatch):
    monkeypatch.setenv("ENABLE_PPSTRUCTURE", "1")
    assert te._ppstructure_enabled() is True
    monkeypatch.setenv("ENABLE_PPSTRUCTURE", "0")
    assert te._ppstructure_enabled() is False


def test_merge_fills_report_filing_date_from_vision():
    """Vision-extracted 報核日 must merge into ReviewTableData (item 1 on scans)."""
    base = _base(report_filing_date=None)
    merged = te._merge_into(base, {"report_filing_date": "112年9月8日"})
    assert merged.report_filing_date == "112年9月8日"


def test_vision_parse_keeps_report_filing_date():
    parsed = te._parse_vision_fields(
        {"report_filing_date": "112年9月8日", "bonus_limit": None, "base_floor_area": 1000.0}
    )
    assert parsed == {"report_filing_date": "112年9月8日", "base_floor_area": 1000.0}


def test_enhance_returns_base_when_no_page():
    base = _base(raw_page=None)
    assert te.enhance_review_table("x.pdf", base) is base


def test_enhance_escalates_to_vision_on_low_coverage(monkeypatch):
    base = _base()  # nothing filled → coverage 0 → must escalate
    with patch.object(te, "_extract_via_ppstructure", return_value={}), \
         patch.object(te, "_extract_via_vision",
                      return_value={"bonus_floor_area": "3930.25", "legal_parking": "42"}) as mock_vision:
        result = te.enhance_review_table("x.pdf", base)

    mock_vision.assert_called_once()
    assert result.bonus_floor_area == 3930.25
    assert result.legal_parking == 42


def test_enhance_skips_vision_when_onprem_sufficient(monkeypatch):
    base = _base()
    onprem = {
        "bonus_floor_area": "3930.25", "bonus_limit": "1877.63",
        "legal_parking": "42", "actual_parking": "47", "land_area": "1052.00",
    }
    with patch.object(te, "_extract_via_ppstructure", return_value=onprem), \
         patch.object(te, "_extract_via_vision") as mock_vision:
        result = te.enhance_review_table("x.pdf", base)

    mock_vision.assert_not_called()  # on-prem covered all critical fields
    assert result.land_area == 1052.0
    assert result.actual_parking == 47


def test_enhance_partial_onprem_still_escalates(monkeypatch):
    base = _base()
    # only 2/5 critical → below 0.6 threshold → escalate
    onprem = {"legal_parking": "42", "actual_parking": "47"}
    with patch.object(te, "_extract_via_ppstructure", return_value=onprem), \
         patch.object(te, "_extract_via_vision",
                      return_value={"land_area": "1052", "bonus_floor_area": "3930", "bonus_limit": "1877"}) as mock_vision:
        result = te.enhance_review_table("x.pdf", base)

    mock_vision.assert_called_once()
    assert result.legal_parking == 42       # from on-prem
    assert result.land_area == 1052.0       # from vision
