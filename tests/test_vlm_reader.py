"""Tests for the on-prem VLM review-table client (地端 OCR)."""
from __future__ import annotations

import json

import pytest

from auditor.parsers import vlm_reader


# ── config helpers ──────────────────────────────────────────────────────────

def test_vlm_enabled(monkeypatch):
    monkeypatch.delenv("VLM_ENDPOINT", raising=False)
    assert vlm_reader.vlm_enabled() is False
    monkeypatch.setenv("VLM_ENDPOINT", "http://gpu:8000")
    assert vlm_reader.vlm_enabled() is True
    monkeypatch.setenv("VLM_ENDPOINT", "   ")
    assert vlm_reader.vlm_enabled() is False


def test_endpoint_appends_path(monkeypatch):
    monkeypatch.setenv("VLM_ENDPOINT", "http://gpu:8000")
    assert vlm_reader._endpoint() == "http://gpu:8000/v1/chat/completions"
    monkeypatch.setenv("VLM_ENDPOINT", "http://gpu:8000/")
    assert vlm_reader._endpoint() == "http://gpu:8000/v1/chat/completions"
    monkeypatch.setenv("VLM_ENDPOINT", "http://gpu:8000/v1/chat/completions")
    assert vlm_reader._endpoint() == "http://gpu:8000/v1/chat/completions"


@pytest.mark.parametrize("value,expected", [
    ("A-1：送版/(第次)公開展覽", "A-1"),
    ("B-1: 168專案小組版", "B-1"),
    ("B-2", "B-2"),
    ("其他文字", "其他文字"),
])
def test_normalize_submission_type(value, expected):
    assert vlm_reader._normalize_submission_type(value) == expected


@pytest.mark.parametrize("raw,expected", [
    ('{"legal_parking": 33}', {"legal_parking": 33}),
    ('```json\n{"legal_parking": 33}\n```', {"legal_parking": 33}),
    ('說明:\n{"a": 1}\n以上', {"a": 1}),
    ('not json at all', None),
    ('[1, 2, 3]', None),  # array, not an object
])
def test_load_json_obj(raw, expected):
    assert vlm_reader._load_json_obj(raw) == expected


# ── extraction ──────────────────────────────────────────────────────────────

class _FakeResp:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def _resp_with_content(content: str) -> _FakeResp:
    return _FakeResp({"choices": [{"message": {"content": content}}]})


def test_extract_disabled_returns_empty(monkeypatch):
    monkeypatch.delenv("VLM_ENDPOINT", raising=False)
    assert vlm_reader.extract_review_table_fields("x.pdf", 1) == {}


def test_extract_render_failure_returns_empty(monkeypatch):
    monkeypatch.setenv("VLM_ENDPOINT", "http://gpu:8000")
    monkeypatch.setattr(vlm_reader, "_render_page_jpeg_b64", lambda p, n: None)
    assert vlm_reader.extract_review_table_fields("x.pdf", 1) == {}


def test_extract_happy_path_drops_nulls_and_normalizes(monkeypatch):
    monkeypatch.setenv("VLM_ENDPOINT", "http://gpu:8000")
    monkeypatch.setattr(vlm_reader, "_render_page_jpeg_b64", lambda p, n: "ZmFrZQ==")
    content = json.dumps({
        "legal_parking": "33 輛",
        "bonus_floor_area": "1,446.79 m²",
        "implementer": "測試都市更新會",
        "submission_type": "A-1：公開展覽",
        "land_area": None,          # dropped
        "ev_parking": None,         # dropped
    }, ensure_ascii=False)
    monkeypatch.setattr(
        vlm_reader.urllib.request, "urlopen",
        lambda req, timeout=0: _resp_with_content(content),
    )
    out = vlm_reader.extract_review_table_fields("x.pdf", 1)
    assert out == {
        "legal_parking": "33 輛",
        "bonus_floor_area": "1,446.79 m²",
        "implementer": "測試都市更新會",
        "submission_type": "A-1",   # normalized from "A-1：公開展覽"
    }


def test_extract_request_error_returns_empty(monkeypatch):
    monkeypatch.setenv("VLM_ENDPOINT", "http://gpu:8000")
    monkeypatch.setattr(vlm_reader, "_render_page_jpeg_b64", lambda p, n: "ZmFrZQ==")

    def _boom(req, timeout=0):
        raise OSError("connection refused")

    monkeypatch.setattr(vlm_reader.urllib.request, "urlopen", _boom)
    assert vlm_reader.extract_review_table_fields("x.pdf", 1) == {}


def test_extract_unparseable_content_returns_empty(monkeypatch):
    monkeypatch.setenv("VLM_ENDPOINT", "http://gpu:8000")
    monkeypatch.setattr(vlm_reader, "_render_page_jpeg_b64", lambda p, n: "ZmFrZQ==")
    monkeypatch.setattr(
        vlm_reader.urllib.request, "urlopen",
        lambda req, timeout=0: _resp_with_content("抱歉,我看不懂這張圖"),
    )
    assert vlm_reader.extract_review_table_fields("x.pdf", 1) == {}


# ── table_extractor integration wrapper ─────────────────────────────────────

def test_transcribe_page_disabled(monkeypatch):
    monkeypatch.delenv("VLM_ENDPOINT", raising=False)
    assert vlm_reader.transcribe_page("x.pdf", 1) is None


def test_transcribe_page_happy(monkeypatch):
    monkeypatch.setenv("VLM_ENDPOINT", "http://gpu:8000")
    monkeypatch.setattr(vlm_reader, "_render_page_jpeg_b64", lambda p, n: "ZmFrZQ==")
    monkeypatch.setattr(
        vlm_reader.urllib.request, "urlopen",
        lambda req, timeout=0: _resp_with_content("逐行轉錄的整頁文字"),
    )
    assert vlm_reader.transcribe_page("x.pdf", 1) == "逐行轉錄的整頁文字"


def test_table_extractor_vlm_wrapper_passthrough(monkeypatch):
    from auditor.extractors import table_extractor
    monkeypatch.setattr(
        "auditor.parsers.vlm_reader.extract_review_table_fields",
        lambda p, n: {"legal_parking": 33},
    )
    assert table_extractor._extract_via_vlm("x.pdf", 1) == {"legal_parking": 33}


def test_table_extractor_vlm_wrapper_swallows_errors(monkeypatch):
    from auditor.extractors import table_extractor

    def _boom(p, n):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "auditor.parsers.vlm_reader.extract_review_table_fields", _boom
    )
    assert table_extractor._extract_via_vlm("x.pdf", 1) == {}
