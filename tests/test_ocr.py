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
