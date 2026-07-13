"""Tests for the per-page text provider (routes text-layer vs VLM vs OCR)."""
from __future__ import annotations

from auditor.parsers import page_text as pt


def _patch_native(monkeypatch, text):
    monkeypatch.setattr(pt, "extract_page_text", lambda p, n: text)
    monkeypatch.setattr(pt, "_FITZ_AVAILABLE", False)


def test_read_page_uses_text_layer(monkeypatch):
    _patch_native(monkeypatch, "這是一頁有足夠字數的原生文字內容" * 2)
    r = pt.read_page("x.pdf", 1)
    assert r.source == "text"
    assert "原生文字" in r.text


def test_read_page_falls_to_vlm(monkeypatch):
    _patch_native(monkeypatch, "")  # 掃描頁,無文字層
    monkeypatch.setattr("auditor.parsers.vlm_reader.vlm_enabled", lambda: True)
    monkeypatch.setattr(
        "auditor.parsers.vlm_reader.transcribe_page",
        lambda p, n: "VLM 讀出的整頁文字",
    )
    r = pt.read_page("x.pdf", 2)
    assert r.source == "vlm"
    assert r.text == "VLM 讀出的整頁文字"


def test_read_page_falls_to_ocr_when_vlm_off(monkeypatch):
    _patch_native(monkeypatch, "")
    monkeypatch.setattr("auditor.parsers.vlm_reader.vlm_enabled", lambda: False)
    monkeypatch.setattr("auditor.parsers.ocr_reader.ocr_available", lambda: True)
    monkeypatch.setattr(
        "auditor.parsers.ocr_reader.ocr_page",
        lambda p, idx: "PaddleOCR 讀出的文字",
    )
    r = pt.read_page("x.pdf", 3)
    assert r.source == "ocr"
    assert "PaddleOCR" in r.text


def test_read_page_empty_when_no_engine(monkeypatch):
    _patch_native(monkeypatch, "")
    monkeypatch.setattr("auditor.parsers.vlm_reader.vlm_enabled", lambda: False)
    monkeypatch.setattr("auditor.parsers.ocr_reader.ocr_available", lambda: False)
    r = pt.read_page("x.pdf", 4)
    assert r.source == "empty"
    assert r.text == ""


def test_short_native_text_treated_as_scanned(monkeypatch):
    # 少於門檻的原生文字 → 視為掃描頁,交給 VLM
    _patch_native(monkeypatch, "第3頁")  # < _MIN_NATIVE_CHARS
    monkeypatch.setattr("auditor.parsers.vlm_reader.vlm_enabled", lambda: True)
    monkeypatch.setattr(
        "auditor.parsers.vlm_reader.transcribe_page", lambda p, n: "VLM 補讀"
    )
    r = pt.read_page("x.pdf", 3)
    assert r.source == "vlm"


def test_page_text_convenience(monkeypatch):
    _patch_native(monkeypatch, "這是足夠長的原生文字內容用於便利函式測試")
    assert "原生文字" in pt.page_text("x.pdf", 1)
