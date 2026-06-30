"""
Tests for OCR reader and updated front_docs extractor patterns.
"""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock


# ── ocr_reader module ─────────────────────────────────────────────────────────

def test_ocr_reader_imports():
    from auditor.parsers.ocr_reader import ocr_available, ocr_page, ocr_pages
    assert callable(ocr_available)
    assert callable(ocr_page)
    assert callable(ocr_pages)


def test_ocr_available_returns_bool():
    from auditor.parsers.ocr_reader import ocr_available
    result = ocr_available()
    assert isinstance(result, bool)


def test_ocr_page_returns_empty_when_fitz_unavailable(tmp_path):
    """ocr_page must return '' gracefully if pymupdf is missing."""
    import auditor.parsers.ocr_reader as mod
    with patch.object(mod, "_FITZ_OK", False):
        result = mod.ocr_page("nonexistent.pdf", 0)
    assert result == ""


def test_ocr_pages_returns_empty_dict_when_fitz_unavailable():
    import auditor.parsers.ocr_reader as mod
    with patch.object(mod, "_FITZ_OK", False):
        result = mod.ocr_pages("nonexistent.pdf", [0, 1, 2])
    assert result == {}


# ── front_docs pattern improvements ───────────────────────────────────────────

def test_match_doc_type_combined_variant():
    """Regression: '都市更新事業計畫及權利變換計畫申請書' must match '申請書'."""
    from auditor.extractors.front_docs import _match_doc_type
    text = "都市更新事業計畫及權利變換計畫申請書 ............ I"
    assert _match_doc_type(text) == "申請書"


def test_match_doc_type_original_variant():
    from auditor.extractors.front_docs import _match_doc_type
    assert _match_doc_type("都市更新事業計畫申請書") == "申請書"


def test_match_doc_type_jiangjie_shu():
    from auditor.extractors.front_docs import _match_doc_type
    assert _match_doc_type("切結書") == "切結書"


def test_match_doc_type_weituoshu():
    from auditor.extractors.front_docs import _match_doc_type
    # Real 委託書 page: spaced OCR title + content marker → matches
    assert _match_doc_type("委  託  書\n茲委託都更顧問公司辦理申請事宜") == "委託書"
    # Bare title only → treated as TOC reference, not a real page
    assert _match_doc_type("委  託  書") is None


# ── OCR-permissive date extraction ────────────────────────────────────────────

def test_extract_roc_date_ocr_compact():
    """OCR date: compact form without 中華民國 prefix."""
    from auditor.extractors.front_docs import _extract_roc_date_ocr
    assert _extract_roc_date_ocr("114年5月28日") == "114年5月28日"


def test_extract_roc_date_ocr_with_prefix():
    """OCR date: full form with prefix."""
    from auditor.extractors.front_docs import _extract_roc_date_ocr
    assert _extract_roc_date_ocr("中華民國114年5月28日") == "114年5月28日"


def test_extract_roc_date_ocr_spaced():
    """OCR date: spaces between chars (common in spread layouts)."""
    from auditor.extractors.front_docs import _extract_roc_date_ocr
    assert _extract_roc_date_ocr("114 年 5 月 28 日") == "114年5月28日"


def test_extract_roc_date_ocr_rejects_invalid_year():
    from auditor.extractors.front_docs import _extract_roc_date_ocr
    # year 99 is below 100 — too old to be a valid ROC year
    assert _extract_roc_date_ocr("99年5月28日") is None


def test_extract_roc_date_ocr_rejects_invalid_month():
    from auditor.extractors.front_docs import _extract_roc_date_ocr
    assert _extract_roc_date_ocr("114年13月1日") is None


# ── TOC detection in extract_front_docs ──────────────────────────────────────

def test_extract_front_docs_detects_all_docs_from_toc():
    """TOC page should register all doc types even without content pages."""
    from auditor.extractors.front_docs import extract_front_docs

    toc_text = (
        "目 錄\n"
        "都市更新事業計畫及權利變換計畫申請書 ...... I\n"
        "切結書 ........................................... II\n"
        "委託書(都更規劃) ......................... III-1\n"
        "臺北市都市更新審議資料表 ............... IV\n"
    )

    fake_pages = [{"page_num": 1, "text": toc_text, "tables": [], "_image_page": False}]

    with patch("auditor.extractors.front_docs.extract_pages_text", return_value=fake_pages), \
         patch("auditor.extractors.front_docs.scan_pages", return_value=[]):
        fd, _ = extract_front_docs("fake.pdf", use_ocr=False)

    assert fd.has_application
    assert fd.has_affidavit
    assert fd.poa_count >= 1


def test_extract_front_docs_supplement_fallback_date():
    """When front pages are image-based, date inferred from 謄本 reference."""
    from auditor.extractors.front_docs import extract_front_docs

    pages = [
        {"page_num": 1, "text": "目 錄\n切結書 ........ II", "tables": [], "_image_page": False},
        # image page — no text
        {"page_num": 2, "text": "", "tables": [], "_image_page": True},
        # supplementary text mentioning 謄本 date
        {"page_num": 25, "text": "依據114年5月28日謄本修正所有權人", "tables": [], "_image_page": False},
    ]

    with patch("auditor.extractors.front_docs.extract_pages_text", return_value=pages), \
         patch("auditor.extractors.front_docs.scan_pages", return_value=[]):
        fd, _ = extract_front_docs("fake.pdf", use_ocr=False)

    assert fd.report_date == "114年5月28日"
    assert "謄本" in fd.report_date_source


def test_extract_front_docs_no_ocr_flag_skips_ocr():
    """use_ocr=False must not call ocr_pages."""
    from auditor.extractors.front_docs import extract_front_docs

    pages = [{"page_num": i, "text": "", "tables": [], "_image_page": True} for i in range(1, 5)]

    with patch("auditor.extractors.front_docs.extract_pages_text", return_value=pages), \
         patch("auditor.extractors.front_docs.scan_pages", return_value=[]), \
         patch("auditor.parsers.ocr_reader.ocr_pages") as mock_ocr:
        extract_front_docs("fake.pdf", use_ocr=False)
        mock_ocr.assert_not_called()


# ── PaddleOCR integration (Item 1) ───────────────────────────────────────────

def test_parse_paddle_result_empty_on_none():
    from auditor.parsers.ocr_reader import _parse_paddle_result
    assert _parse_paddle_result(None) == ""
    assert _parse_paddle_result([]) == ""


def test_parse_paddle_result_filters_low_confidence():
    """Detections below _OCR_MIN_CONFIDENCE must be excluded."""
    from auditor.parsers.ocr_reader import _parse_paddle_result, _OCR_MIN_CONFIDENCE
    detections = [
        [None, ("高信心", 0.95)],
        [None, ("低信心", _OCR_MIN_CONFIDENCE - 0.1)],
        [None, ("邊界值", _OCR_MIN_CONFIDENCE)],
    ]
    text = _parse_paddle_result(detections)
    assert "高信心" in text
    assert "邊界值" in text
    assert "低信心" not in text


def test_parse_paddle_result_joins_lines():
    from auditor.parsers.ocr_reader import _parse_paddle_result
    detections = [
        [None, ("line one", 0.9)],
        [None, ("line two", 0.8)],
    ]
    assert _parse_paddle_result(detections) == "line one\nline two"


def test_ocr_page_uses_paddle_reader(tmp_path):
    """ocr_page must call reader.ocr() and return parsed text."""
    import auditor.parsers.ocr_reader as mod
    from unittest.mock import MagicMock, patch

    fake_reader = MagicMock()
    fake_reader.ocr.return_value = [[[None, ("測試文字", 0.95)]]]

    fake_doc = MagicMock()
    fake_pix = MagicMock()
    fake_pix.tobytes.return_value = _make_png_bytes()
    fake_doc.__len__ = lambda self: 5
    fake_doc.__getitem__ = lambda self, i: MagicMock(
        get_pixmap=lambda **kw: fake_pix
    )
    fake_doc.__enter__ = lambda self: self
    fake_doc.__exit__ = MagicMock(return_value=False)

    with patch.object(mod, "_FITZ_OK", True), \
         patch.object(mod, "_paddle_reader", fake_reader), \
         patch.object(mod, "_OCR_AVAILABLE", True), \
         patch.object(mod._fitz, "open", return_value=fake_doc):
        result = mod.ocr_page("fake.pdf", 0)

    assert "測試文字" in result
    fake_reader.ocr.assert_called_once()


def test_ocr_pages_batch_call_count(tmp_path):
    """ocr_pages must make exactly one batch reader.ocr() call for multiple uncached pages."""
    import auditor.parsers.ocr_reader as mod
    from unittest.mock import MagicMock, patch

    fake_reader = MagicMock()
    # Batch result: two pages
    fake_reader.ocr.return_value = [
        [[None, ("頁一", 0.9)]],
        [[None, ("頁二", 0.8)]],
    ]

    fake_pix = MagicMock()
    fake_pix.tobytes.return_value = _make_png_bytes()
    fake_page = MagicMock()
    fake_page.get_pixmap.return_value = fake_pix

    fake_doc = MagicMock()
    fake_doc.__len__ = lambda self: 10
    fake_doc.__getitem__ = lambda self, i: fake_page
    fake_doc.close = MagicMock()

    with patch.object(mod, "_FITZ_OK", True), \
         patch.object(mod, "_paddle_reader", fake_reader), \
         patch.object(mod, "_OCR_AVAILABLE", True), \
         patch.object(mod, "_file_hash", return_value=None), \
         patch.object(mod._fitz, "open", return_value=fake_doc):
        result = mod.ocr_pages("fake.pdf", [0, 1])

    assert fake_reader.ocr.call_count == 1, "Batch must use exactly one reader.ocr() call"
    assert "頁一" in result.get(0, "")
    assert "頁二" in result.get(1, "")


def _make_png_bytes() -> bytes:
    """Return minimal valid PNG bytes (1x1 white pixel) for mocking."""
    from PIL import Image
    import io
    img = Image.new("RGB", (1, 1), color=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── confidence filtering (Item 2) ─────────────────────────────────────────────

def test_ocr_min_confidence_constant_exists():
    from auditor.parsers.ocr_reader import _OCR_MIN_CONFIDENCE
    assert 0.0 < _OCR_MIN_CONFIDENCE < 1.0


def test_parse_paddle_result_skips_none_detection():
    """None entries in the detection list must be skipped gracefully."""
    from auditor.parsers.ocr_reader import _parse_paddle_result
    detections = [None, [None, ("valid", 0.9)], None]
    assert _parse_paddle_result(detections) == "valid"


# ── cache version key (Item 7) ────────────────────────────────────────────────

def test_cache_schema_includes_preprocess_version(tmp_path):
    """The SQLite cache table must have a preprocess_version column."""
    import sqlite3
    import auditor.parsers.ocr_reader as mod
    from unittest.mock import patch

    db_path = tmp_path / "ocr_cache.db"
    with patch.object(mod, "_CACHE_DB", db_path), \
         patch.object(mod, "_CACHE_DIR", tmp_path):
        conn = mod._get_cache_conn()
        assert conn is not None
        cols = {row[1] for row in conn.execute("PRAGMA table_info(ocr_cache)")}
        assert "preprocess_version" in cols
        conn.close()


def test_cache_get_uses_preprocess_version(tmp_path):
    """_cache_get must not return entries from a different preprocess_version."""
    import sqlite3
    import auditor.parsers.ocr_reader as mod
    from unittest.mock import patch

    db_path = tmp_path / "ocr_cache.db"
    with patch.object(mod, "_CACHE_DB", db_path), \
         patch.object(mod, "_CACHE_DIR", tmp_path):
        conn = mod._get_cache_conn()
        # Write with version 1 directly
        conn.execute(
            "INSERT INTO ocr_cache (file_hash, page_idx, zoom, preprocess_version, text)"
            " VALUES (?,?,?,?,?)",
            ("abc", 0, 2.0, 1, "old-text"),
        )
        conn.commit()
        # _cache_get with current _PREPROCESS_VERSION must not return old entry
        result = mod._cache_get(conn, "abc", 0, 2.0)
        assert result is None
        conn.close()


def test_cache_put_uses_preprocess_version(tmp_path):
    """_cache_put must store the current _PREPROCESS_VERSION."""
    import auditor.parsers.ocr_reader as mod
    from unittest.mock import patch

    db_path = tmp_path / "ocr_cache.db"
    with patch.object(mod, "_CACHE_DB", db_path), \
         patch.object(mod, "_CACHE_DIR", tmp_path):
        conn = mod._get_cache_conn()
        mod._cache_put(conn, "abc", 0, 2.0, "hello")
        row = conn.execute(
            "SELECT preprocess_version FROM ocr_cache WHERE file_hash=? AND page_idx=? AND zoom=?",
            ("abc", 0, 2.0),
        ).fetchone()
        assert row is not None
        assert row[0] == mod._PREPROCESS_VERSION
        conn.close()


def test_cache_migration_drops_old_schema(tmp_path):
    """If the existing table is missing preprocess_version, it must be recreated."""
    import sqlite3
    import auditor.parsers.ocr_reader as mod
    from unittest.mock import patch

    db_path = tmp_path / "ocr_cache.db"
    # Create old-schema table
    old_conn = sqlite3.connect(str(db_path))
    old_conn.execute(
        "CREATE TABLE ocr_cache"
        " (file_hash TEXT, page_idx INTEGER, zoom REAL, text TEXT,"
        "  PRIMARY KEY (file_hash, page_idx, zoom))"
    )
    old_conn.execute("INSERT INTO ocr_cache VALUES ('x', 0, 2.0, 'old')")
    old_conn.commit()
    old_conn.close()

    with patch.object(mod, "_CACHE_DB", db_path), \
         patch.object(mod, "_CACHE_DIR", tmp_path):
        conn = mod._get_cache_conn()
        assert conn is not None
        cols = {row[1] for row in conn.execute("PRAGMA table_info(ocr_cache)")}
        assert "preprocess_version" in cols
        # Old data should be gone
        count = conn.execute("SELECT COUNT(*) FROM ocr_cache").fetchone()[0]
        assert count == 0
        conn.close()
