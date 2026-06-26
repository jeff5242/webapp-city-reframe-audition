"""Tests for the four-phase AI parsing pipeline.

All external dependencies (fitz, docling, surya, unstructured, anthropic)
are mocked so tests run without ML packages installed.
"""
from __future__ import annotations

import types
from unittest.mock import MagicMock, patch

# Reusable patcher: prevents validate_pdf_path from hitting the filesystem
_mock_validate = patch(
    "auditor.parsing_pipeline._path_utils.validate_pdf_path",
    side_effect=lambda p, allowed_dir=None: p,
)


# ── Phase 1: triage ────────────────────────────────────────────────────────────

class TestTriage:
    def _make_block(self, x0, y0, x1, y1, text, btype=0):
        return (x0, y0, x1, y1, text, 0, btype)

    def _make_fitz_page(self, blocks, image_area=0.0, width=595, height=842):
        page = MagicMock()
        page.rect = MagicMock(width=width, height=height)
        page.get_text.return_value = blocks
        page.get_images.return_value = []
        if image_area > 0:
            xref_img = MagicMock()
            xref_img[0] = 1
            page.get_images.return_value = [[1, 0, 0, 0, 0, "", "", ""]]
            r = MagicMock()
            r.get_area.return_value = image_area
            page.get_image_rects.return_value = [r]
        return page

    def _make_fitz_doc(self, pages):
        doc = MagicMock()
        doc.__iter__.return_value = iter(pages)
        doc.__len__.return_value = len(pages)
        return doc

    def test_text_rich_page_not_scanned(self):
        from auditor.parsing_pipeline.triage import triage_pdf, _MIN_TEXT_CHARS

        blocks = [self._make_block(0, 0, 100, 20, "A" * (_MIN_TEXT_CHARS + 10))]
        page = self._make_fitz_page(blocks)
        doc = self._make_fitz_doc([page])

        fitz_mod = MagicMock()
        fitz_mod.open.return_value = doc

        with _mock_validate, patch.dict("sys.modules", {"fitz": fitz_mod}):
            from importlib import reload
            import auditor.parsing_pipeline.triage as mod
            reload(mod)
            results = mod.triage_pdf("dummy.pdf")

        assert len(results) == 1
        assert results[0].is_scanned is False
        assert results[0].page_num == 1

    def test_empty_page_is_scanned(self):
        from auditor.parsing_pipeline.triage import triage_pdf

        page = self._make_fitz_page([])
        doc = self._make_fitz_doc([page])

        fitz_mod = MagicMock()
        fitz_mod.open.return_value = doc

        with _mock_validate, patch.dict("sys.modules", {"fitz": fitz_mod}):
            from importlib import reload
            import auditor.parsing_pipeline.triage as mod
            reload(mod)
            results = mod.triage_pdf("dummy.pdf")

        assert results[0].is_scanned is True
        assert results[0].char_count == 0

    def test_runtime_error_without_fitz(self):
        import sys, importlib
        saved = sys.modules.pop("fitz", None)
        sys.modules["fitz"] = None  # type: ignore
        try:
            import auditor.parsing_pipeline.triage as mod
            from importlib import reload
            reload(mod)
            import pytest
            with pytest.raises((RuntimeError, ImportError)):
                mod.triage_pdf("dummy.pdf")
        finally:
            if saved is not None:
                sys.modules["fitz"] = saved
            else:
                sys.modules.pop("fitz", None)

    def test_scanned_and_text_page_indices(self):
        from auditor.parsing_pipeline.triage import PageClass, scanned_page_indices, text_page_indices

        classes = [
            PageClass(page_num=1, is_scanned=False, char_count=200, text_fraction=0.4, image_fraction=0.0),
            PageClass(page_num=2, is_scanned=True,  char_count=0,   text_fraction=0.0, image_fraction=0.9),
            PageClass(page_num=3, is_scanned=False, char_count=150, text_fraction=0.3, image_fraction=0.1),
        ]
        assert scanned_page_indices(classes) == [1]   # 0-based page 2
        assert text_page_indices(classes) == [0, 2]   # 0-based pages 1 and 3

    def test_multiple_pages_correct_page_nums(self):
        from auditor.parsing_pipeline.triage import triage_pdf

        pages = [
            self._make_fitz_page([self._make_block(0, 0, 100, 20, "X" * 100)]),
            self._make_fitz_page([]),
            self._make_fitz_page([self._make_block(0, 0, 200, 50, "Y" * 80)]),
        ]
        doc = self._make_fitz_doc(pages)
        fitz_mod = MagicMock()
        fitz_mod.open.return_value = doc

        with _mock_validate, patch.dict("sys.modules", {"fitz": fitz_mod}):
            from importlib import reload
            import auditor.parsing_pipeline.triage as mod
            reload(mod)
            results = mod.triage_pdf("dummy.pdf")

        assert [r.page_num for r in results] == [1, 2, 3]
        assert [r.is_scanned for r in results] == [False, True, False]


# ── Path validation tests ──────────────────────────────────────────────────────

class TestPathValidation:
    def test_nonexistent_file_raises(self):
        from auditor.parsing_pipeline._path_utils import validate_pdf_path
        import pytest
        with pytest.raises(FileNotFoundError):
            validate_pdf_path("/nonexistent/path/file.pdf")

    def test_non_pdf_extension_raises(self, tmp_path):
        bad = tmp_path / "doc.txt"
        bad.write_text("hello")
        import pytest
        from auditor.parsing_pipeline._path_utils import validate_pdf_path
        with pytest.raises(ValueError, match=".pdf"):
            validate_pdf_path(str(bad))

    def test_valid_pdf_returns_resolved_path(self, tmp_path):
        from auditor.parsing_pipeline._path_utils import validate_pdf_path
        f = tmp_path / "test.pdf"
        f.write_bytes(b"%PDF-1.4")
        result = validate_pdf_path(str(f))
        assert result == str(f.resolve())

    def test_path_traversal_blocked(self, tmp_path):
        import pytest
        from auditor.parsing_pipeline._path_utils import validate_pdf_path
        allowed = tmp_path / "uploads"
        allowed.mkdir()
        outside = tmp_path / "secret.pdf"
        outside.write_bytes(b"%PDF-1.4")
        with pytest.raises(ValueError, match="outside the allowed directory"):
            validate_pdf_path(str(outside), allowed_dir=str(allowed))

    def test_path_inside_allowed_dir_ok(self, tmp_path):
        from auditor.parsing_pipeline._path_utils import validate_pdf_path
        allowed = tmp_path / "uploads"
        allowed.mkdir()
        f = allowed / "doc.pdf"
        f.write_bytes(b"%PDF-1.4")
        result = validate_pdf_path(str(f), allowed_dir=str(allowed))
        assert result == str(f.resolve())


# ── _contiguous_runs tests ─────────────────────────────────────────────────────

class TestContiguousRuns:
    def test_single_page(self):
        from auditor.parsing_pipeline.docling_reader import _contiguous_runs
        assert _contiguous_runs([5]) == [(5, 5)]

    def test_already_contiguous(self):
        from auditor.parsing_pipeline.docling_reader import _contiguous_runs
        assert _contiguous_runs([1, 2, 3]) == [(1, 3)]

    def test_non_contiguous(self):
        from auditor.parsing_pipeline.docling_reader import _contiguous_runs
        assert _contiguous_runs([2, 7, 15]) == [(2, 2), (7, 7), (15, 15)]

    def test_mixed(self):
        from auditor.parsing_pipeline.docling_reader import _contiguous_runs
        assert _contiguous_runs([1, 2, 5, 6, 7, 10]) == [(1, 2), (5, 7), (10, 10)]

    def test_deduplicates(self):
        from auditor.parsing_pipeline.docling_reader import _contiguous_runs
        assert _contiguous_runs([3, 3, 4]) == [(3, 4)]

    def test_empty(self):
        from auditor.parsing_pipeline.docling_reader import _contiguous_runs
        assert _contiguous_runs([]) == []


# ── Phase 2a: docling_reader ───────────────────────────────────────────────────

class TestDoclingReader:
    def test_is_available_false_when_not_installed(self):
        with patch.dict("sys.modules", {"docling": None}):
            from importlib import reload
            import auditor.parsing_pipeline.docling_reader as mod
            reload(mod)
            assert mod.is_available() is False

    def test_parse_pdf_to_markdown_raises_when_not_installed(self):
        import pytest

        bad_mod = types.ModuleType("docling.document_converter")

        def bad_dc(*a, **kw):
            raise ImportError("docling not installed")

        bad_mod.DocumentConverter = bad_dc
        bad_mod.PdfFormatOption = MagicMock()

        docling_base = types.ModuleType("docling")
        docling_datamodel = types.ModuleType("docling.datamodel")
        docling_po = types.ModuleType("docling.datamodel.pipeline_options")
        docling_po.PdfPipelineOptions = MagicMock()

        with patch.dict("sys.modules", {
            "docling": docling_base,
            "docling.document_converter": bad_mod,
            "docling.datamodel": docling_datamodel,
            "docling.datamodel.pipeline_options": docling_po,
        }):
            from importlib import reload
            import auditor.parsing_pipeline.docling_reader as mod
            reload(mod)
            with pytest.raises((ImportError, Exception)):
                mod.parse_pdf_to_markdown("dummy.pdf")

    def test_parse_pdf_to_markdown_returns_string(self):
        mock_doc = MagicMock()
        mock_doc.export_to_markdown.return_value = "# 申請書\n\n內容"
        mock_result = MagicMock()
        mock_result.document = mock_doc

        mock_converter_instance = MagicMock()
        mock_converter_instance.convert.return_value = mock_result
        MockConverter = MagicMock(return_value=mock_converter_instance)

        mock_pdf_option = MagicMock()
        mock_pipeline = MagicMock()

        docling_mod = types.ModuleType("docling")
        dc_mod = types.ModuleType("docling.document_converter")
        dc_mod.DocumentConverter = MockConverter
        dc_mod.PdfFormatOption = mock_pdf_option
        po_mod = types.ModuleType("docling.datamodel.pipeline_options")
        po_mod.PdfPipelineOptions = mock_pipeline
        docling_datamodel = types.ModuleType("docling.datamodel")

        with _mock_validate, patch.dict("sys.modules", {
            "docling": docling_mod,
            "docling.document_converter": dc_mod,
            "docling.datamodel": docling_datamodel,
            "docling.datamodel.pipeline_options": po_mod,
        }):
            from importlib import reload
            import auditor.parsing_pipeline.docling_reader as mod
            reload(mod)
            result = mod.parse_pdf_to_markdown("dummy.pdf")

        assert isinstance(result, str)
        assert "申請書" in result


# ── Phase 2b: surya_reader ─────────────────────────────────────────────────────

class TestSuryaReader:
    def test_is_available_false_when_not_installed(self):
        with patch.dict("sys.modules", {"surya": None}):
            from importlib import reload
            import auditor.parsing_pipeline.surya_reader as mod
            reload(mod)
            assert mod.is_available() is False

    def test_empty_page_indices_returns_empty(self):
        from auditor.parsing_pipeline.surya_reader import ocr_pages
        result = ocr_pages("dummy.pdf", [])
        assert result == {}

    def test_ocr_pages_returns_text_dict(self):
        # Mock all surya and fitz imports
        fitz_page = MagicMock()
        pix = MagicMock()
        pix.width = 100
        pix.height = 100
        pix.samples = b"\xff" * (100 * 100 * 3)
        fitz_page.get_pixmap.return_value = pix
        fitz_doc = MagicMock()
        fitz_doc.__len__.return_value = 3
        fitz_doc.__getitem__.side_effect = lambda i: fitz_page
        fitz_doc.__enter__ = lambda s: s
        fitz_doc.__exit__ = MagicMock(return_value=False)

        line1 = MagicMock()
        line1.text = "都市更新申請書"
        pred = MagicMock()
        pred.text_lines = [line1]

        fitz_mod = MagicMock()
        fitz_mod.open.return_value = fitz_doc

        surya_mod = types.ModuleType("surya")
        surya_ocr = types.ModuleType("surya.ocr")
        surya_ocr.run_ocr = MagicMock(return_value=[pred])
        surya_det = types.ModuleType("surya.model.detection.model")
        surya_det.load_model = MagicMock(return_value=MagicMock())
        surya_det.load_processor = MagicMock(return_value=MagicMock())
        surya_rec = types.ModuleType("surya.model.recognition.model")
        surya_rec.load_model = MagicMock(return_value=MagicMock())
        surya_rec_proc = types.ModuleType("surya.model.recognition.processor")
        surya_rec_proc.load_processor = MagicMock(return_value=MagicMock())

        pil_mod = MagicMock()
        pil_image = MagicMock()
        pil_image.frombytes = MagicMock(return_value=MagicMock())
        pil_mod.Image = pil_image

        with _mock_validate, patch.dict("sys.modules", {
            "fitz": fitz_mod,
            "surya": surya_mod,
            "surya.ocr": surya_ocr,
            "surya.model": types.ModuleType("surya.model"),
            "surya.model.detection": types.ModuleType("surya.model.detection"),
            "surya.model.detection.model": surya_det,
            "surya.model.recognition": types.ModuleType("surya.model.recognition"),
            "surya.model.recognition.model": surya_rec,
            "surya.model.recognition.processor": surya_rec_proc,
            "PIL": pil_mod,
        }):
            from importlib import reload
            import auditor.parsing_pipeline.surya_reader as mod
            reload(mod)
            result = mod.ocr_pages("dummy.pdf", [0])

        assert 0 in result
        assert "都市更新" in result[0]


# ── Phase 3: chunker ───────────────────────────────────────────────────────────

class TestChunker:
    def test_is_available_false_when_not_installed(self):
        with patch.dict("sys.modules", {"unstructured": None}):
            from importlib import reload
            import auditor.parsing_pipeline.chunker as mod
            reload(mod)
            assert mod.is_available() is False

    def test_fallback_chunker_splits_on_headings(self):
        from auditor.parsing_pipeline.chunker import _chunk_fallback, DocumentChunk

        md = """# 第一章 事業計畫

計畫概要內容。

## 1.1 更新單元

更新單元細節。

# 第二章 財務計畫

財務說明。
"""
        chunks = _chunk_fallback(md)
        assert len(chunks) >= 3
        titles = [c.section_title for c in chunks]
        assert "第一章 事業計畫" in titles
        assert "1.1 更新單元" in titles
        assert "第二章 財務計畫" in titles

    def test_fallback_parent_title_hierarchy(self):
        from auditor.parsing_pipeline.chunker import _chunk_fallback

        md = "# 主章節\n\n內容\n\n## 子章節\n\n子內容"
        chunks = _chunk_fallback(md)
        sub = next(c for c in chunks if c.section_title == "子章節")
        assert sub.parent_title == "主章節"

    def test_fallback_heading_level_stepback(self):
        """## D after ### C must have the same parent as ## B, not C."""
        from auditor.parsing_pipeline.chunker import _chunk_fallback

        md = (
            "# A\n\nA 內容\n\n"
            "## B\n\nB 內容\n\n"
            "### C\n\nC 內容\n\n"
            "## D\n\nD 內容\n"
        )
        chunks = _chunk_fallback(md)
        by_title = {c.section_title: c for c in chunks}

        assert by_title["B"].parent_title == "A"
        assert by_title["C"].parent_title == "B"
        assert by_title["D"].parent_title == "A"   # regression: was "C" before fix

    def test_fallback_sibling_headings_same_parent(self):
        """Two ## headings under the same # must both have that # as parent."""
        from auditor.parsing_pipeline.chunker import _chunk_fallback

        md = "# 章節\n\n內容\n\n## 子A\n\nA 內容\n\n## 子B\n\nB 內容\n"
        chunks = _chunk_fallback(md)
        by_title = {c.section_title: c for c in chunks}
        assert by_title["子A"].parent_title == "章節"
        assert by_title["子B"].parent_title == "章節"

    def test_fallback_empty_markdown_returns_empty(self):
        from auditor.parsing_pipeline.chunker import _chunk_fallback
        assert _chunk_fallback("") == []
        assert _chunk_fallback("   \n  ") == []

    def test_chunk_markdown_uses_fallback_without_unstructured(self):
        """chunk_markdown falls back gracefully when unstructured is missing."""
        with patch.dict("sys.modules", {"unstructured": None,
                                         "unstructured.partition": None,
                                         "unstructured.partition.md": None,
                                         "unstructured.chunking": None,
                                         "unstructured.chunking.title": None}):
            from importlib import reload
            import auditor.parsing_pipeline.chunker as mod
            reload(mod)
            chunks = mod.chunk_markdown("# 章節\n\n內容")
        assert len(chunks) >= 1
        assert chunks[0].section_title == "章節"

    def test_chunk_char_count_correct(self):
        from auditor.parsing_pipeline.chunker import _chunk_fallback

        md = "# 測試\n\n這是測試內容。"
        chunks = _chunk_fallback(md)
        for c in chunks:
            assert c.char_count == len(c.text)

    def test_unknown_strategy_raises(self):
        import pytest
        from auditor.parsing_pipeline.chunker import chunk_markdown
        with pytest.raises(ValueError, match="Unknown chunking strategy"):
            chunk_markdown("# 章節\n\n內容", strategy="unknown_strat")


# ── Phase 4: llm_auditor ──────────────────────────────────────────────────────

class TestLlmAuditor:
    def _make_chunk(self, text="臺北市都市更新審議"):
        c = MagicMock()
        c.text = text
        c.section_title = "test"
        return c

    def _make_anthropic_response(self, findings_list):
        block = MagicMock()
        block.type = "tool_use"
        block.input = {"findings": findings_list}
        response = MagicMock()
        response.content = [block]
        return response

    def test_parse_finding_valid(self):
        from auditor.parsing_pipeline.llm_auditor import _parse_finding, LlmFinding
        raw = {
            "rule_id": "TERM-001",
            "error_type": "typo",
            "severity": "warning",
            "detected_text": "都更",
            "suggested_text": "都市更新",
            "reason": "應使用全稱",
            "page_number": 3,
        }
        f = _parse_finding(raw)
        assert isinstance(f, LlmFinding)
        assert f.rule_id == "TERM-001"
        assert f.page_number == 3

    def test_parse_finding_missing_field_returns_none(self):
        from auditor.parsing_pipeline.llm_auditor import _parse_finding
        assert _parse_finding({}) is None
        assert _parse_finding({"rule_id": "X"}) is None

    def test_parse_finding_invalid_error_type_returns_none(self):
        from auditor.parsing_pipeline.llm_auditor import _parse_finding
        raw = {
            "rule_id": "X", "error_type": "injection_attempt",
            "severity": "critical", "detected_text": "x",
            "suggested_text": "y", "reason": "z", "page_number": 1,
        }
        assert _parse_finding(raw) is None

    def test_parse_finding_invalid_severity_returns_none(self):
        from auditor.parsing_pipeline.llm_auditor import _parse_finding
        raw = {
            "rule_id": "X", "error_type": "typo",
            "severity": "blocker", "detected_text": "x",
            "suggested_text": "y", "reason": "z", "page_number": 1,
        }
        assert _parse_finding(raw) is None

    def test_audit_chunks_sorted_by_page(self):
        from auditor.parsing_pipeline.llm_auditor import audit_chunks

        findings_data = [
            {"rule_id": "LAW-002", "error_type": "regulatory_violation",
             "severity": "critical", "detected_text": "違規文字",
             "suggested_text": "正確文字", "reason": "法規說明", "page_number": 10},
            {"rule_id": "TERM-001", "error_type": "typo",
             "severity": "warning", "detected_text": "錯字",
             "suggested_text": "正確", "reason": "說明", "page_number": 2},
        ]

        mock_response = self._make_anthropic_response(findings_data)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        MockAnthropicClass = MagicMock(return_value=mock_client)

        anthropic_mod = types.ModuleType("anthropic")
        anthropic_mod.Anthropic = MockAnthropicClass

        with patch.dict("sys.modules", {"anthropic": anthropic_mod}):
            from importlib import reload
            import auditor.parsing_pipeline.llm_auditor as mod
            reload(mod)
            results = mod.audit_chunks(
                [self._make_chunk()],
                wiki_rules="## 111年規則\n- 不得使用縮寫",
            )

        assert len(results) == 2
        assert results[0].page_number <= results[1].page_number  # sorted

    def test_audit_chunks_empty_text_skipped(self):
        from auditor.parsing_pipeline.llm_auditor import audit_chunks

        mock_client = MagicMock()
        anthropic_mod = types.ModuleType("anthropic")
        anthropic_mod.Anthropic = MagicMock(return_value=mock_client)

        with patch.dict("sys.modules", {"anthropic": anthropic_mod}):
            from importlib import reload
            import auditor.parsing_pipeline.llm_auditor as mod
            reload(mod)
            results = mod.audit_chunks(
                [self._make_chunk(text="   ")],  # empty text → skipped
                wiki_rules="",
            )

        mock_client.messages.create.assert_not_called()
        assert results == []

    def test_audit_chunks_raises_without_anthropic(self):
        import pytest
        with patch.dict("sys.modules", {"anthropic": None}):
            from importlib import reload
            import auditor.parsing_pipeline.llm_auditor as mod
            reload(mod)
            with pytest.raises((ImportError, TypeError)):
                mod.audit_chunks([self._make_chunk()], wiki_rules="")

    def test_llm_finding_is_frozen(self):
        from auditor.parsing_pipeline.llm_auditor import LlmFinding
        import pytest
        f = LlmFinding("R", "typo", "warning", "x", "y", "z", 1)
        with pytest.raises((AttributeError, TypeError)):
            f.rule_id = "changed"  # type: ignore
