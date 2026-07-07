"""標註頁截圖 raster（副總 UX highlight 定位）。"""
from __future__ import annotations

import fitz  # PyMuPDF

from auditor.reporters.evidence_snapshot import render_evidence_thumbnails


def _make_pdf(pages: int = 3) -> bytes:
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"page {i + 1}")
    data = doc.tobytes()
    doc.close()
    return data


# ── happy path ──────────────────────────────────────────────────────────────

def test_renders_requested_pages_as_data_uris():
    pdf = _make_pdf(3)
    out = render_evidence_thumbnails(pdf, [1, 3])
    assert set(out) == {1, 3}
    for uri in out.values():
        assert uri.startswith("data:image/")
        assert ";base64," in uri
        assert len(uri) > 100  # 實際有影像內容


def test_dedupes_and_sorts_pages():
    pdf = _make_pdf(2)
    out = render_evidence_thumbnails(pdf, [2, 1, 2, 1])
    assert set(out) == {1, 2}


def test_skips_out_of_range_pages():
    pdf = _make_pdf(2)
    out = render_evidence_thumbnails(pdf, [1, 5, 99])
    assert set(out) == {1}


def test_ignores_non_positive_pages():
    pdf = _make_pdf(2)
    out = render_evidence_thumbnails(pdf, [0, -1, 1])
    assert set(out) == {1}


def test_respects_max_pages_cap():
    pdf = _make_pdf(5)
    out = render_evidence_thumbnails(pdf, [1, 2, 3, 4, 5], max_pages=2)
    # 去重排序後取前 2 頁
    assert set(out) == {1, 2}


# ── graceful degradation ─────────────────────────────────────────────────────

def test_empty_bytes_returns_empty():
    assert render_evidence_thumbnails(b"", [1]) == {}


def test_none_bytes_returns_empty():
    assert render_evidence_thumbnails(None, [1]) == {}


def test_no_pages_returns_empty():
    pdf = _make_pdf(2)
    assert render_evidence_thumbnails(pdf, []) == {}


def test_corrupt_bytes_returns_empty_not_raises():
    assert render_evidence_thumbnails(b"not a pdf", [1]) == {}
