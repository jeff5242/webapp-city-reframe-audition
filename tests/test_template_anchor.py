"""模板錨定擷取（Track A）：對齊驗證 + 基準容積補值。"""
from __future__ import annotations

import auditor.parsers.ocr_reader as ocr_mod
from auditor.extractors.template_anchor import _aligned, _num, extract_base_floor_area


def test_num_handles_spaced_comma():
    # 小塊 OCR 常把「2,812」讀成「2 812」→ 去空白後仍要正確
    assert _num("2, 812. 00m²") == 2812.0
    assert _num("1,406.00m") == 1406.0
    assert _num("無") is None


def test_aligned_within_tolerance():
    assert _aligned(1406.0, 1406.0)
    assert _aligned(1410.0, 1406.0)      # ±1%
    assert not _aligned(1300.0, 1406.0)  # 差太多
    assert not _aligned(None, 1406.0)


def _patch(monkeypatch, regions):
    monkeypatch.setattr(ocr_mod, "ocr_regions", lambda *a, **k: regions)


def test_fills_base_when_template_aligns(monkeypatch):
    # 模板抓的獎勵樓地板 == 已知值 → 對齊 → 採信基準容積
    _patch(monkeypatch, {"bonus_floor_area": "1,406.00m²", "base_floor_area": "2,812.00m²"})
    assert extract_base_floor_area("x.pdf", 19, 1406.0) == {"base_floor_area": 2812.0}


def test_no_fill_when_misaligned(monkeypatch):
    # 不同版面：模板獎勵樓地板對不上已知值 → 不填（防誤填）
    _patch(monkeypatch, {"bonus_floor_area": "999.00", "base_floor_area": "5000.00"})
    assert extract_base_floor_area("x.pdf", 19, 1406.0) == {}


def test_no_fill_without_known_bonus(monkeypatch):
    _patch(monkeypatch, {"bonus_floor_area": "1,406.00", "base_floor_area": "2,812.00"})
    assert extract_base_floor_area("x.pdf", 19, None) == {}


def test_no_fill_when_base_not_greater_than_bonus(monkeypatch):
    # 基準容積必大於獎勵樓地板；否則視為裁錯格、不填
    _patch(monkeypatch, {"bonus_floor_area": "1,406.00", "base_floor_area": "1,000.00"})
    assert extract_base_floor_area("x.pdf", 19, 1406.0) == {}
