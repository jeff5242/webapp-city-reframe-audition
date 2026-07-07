"""Geometric bbox table reconstruction for the 審議資料表 (副總 #3)."""
from __future__ import annotations

from auditor.extractors.table_reconstruct import reconstruct_fields
from auditor.parsers.ocr_reader import _box_geometry, _detections_from_result


def _det(text, x0, x1, yc, h=20, conf=0.99):
    return {"text": text, "conf": conf, "x0": x0, "x1": x1, "yc": yc, "h": h}


# ── reconstruct_fields: label → nearest right value on same row ───────────────

def test_simple_label_value_right():
    dets = [
        _det("法定汽車停車位", 0, 100, 50),
        _det("58", 110, 140, 50),
    ]
    assert reconstruct_fields(dets)["legal_parking"] == 58


def test_dense_row_two_pairs_map_to_nearest_value():
    # [法定汽車停車位][58] ... [無障礙停車位][2] on one visual row
    dets = [
        _det("法定汽車停車位", 0, 100, 50),
        _det("58", 110, 140, 50),
        _det("無障礙停車位", 200, 300, 50),
        _det("2", 310, 330, 50),
    ]
    out = reconstruct_fields(dets)
    assert out["legal_parking"] == 58
    assert out["accessible_parking"] == 2


def test_value_from_trailing_number_in_label_box():
    # No separate value cell — number is inside the label detection itself.
    dets = [_det("基準容積 3755.25", 0, 200, 50)]
    assert reconstruct_fields(dets)["base_floor_area"] == 3755.25


def test_float_value_with_units_is_coerced():
    dets = [
        _det("獎勵樓地板面積合計", 0, 150, 50),
        _det("1,928.58 m²", 160, 260, 50),
    ]
    assert reconstruct_fields(dets)["bonus_floor_area"] == 1928.58


def test_non_numeric_neighbour_yields_no_field():
    # Right neighbour is another label (no digits) → numeric coercion drops it.
    dets = [
        _det("法定汽車停車位", 0, 100, 50),
        _det("無障礙停車位", 110, 210, 50),
    ]
    assert "legal_parking" not in reconstruct_fields(dets)


def test_value_on_different_row_not_matched():
    dets = [
        _det("法定汽車停車位", 0, 100, 50),
        _det("58", 110, 140, 400),  # far below → different row
    ]
    assert "legal_parking" not in reconstruct_fields(dets)


def test_string_fields_are_not_trusted_by_geometry():
    # implementer is a string field; geometry pass must ignore it (avoid garbage).
    dets = [
        _det("實施者", 0, 60, 50),
        _det("○○建設股份有限公司", 70, 300, 50),
    ]
    assert reconstruct_fields(dets) == {}


def test_empty_and_geometryless_input():
    assert reconstruct_fields([]) == {}
    assert reconstruct_fields([{"text": "法定汽車停車位", "conf": 0.9}]) == {}


# ── _box_geometry / _detections_from_result ──────────────────────────────────

def test_box_geometry_from_four_corners():
    geom = _box_geometry([[10, 20], [110, 20], [110, 40], [10, 40]])
    assert geom == {"x0": 10.0, "x1": 110.0, "yc": 30.0, "h": 20.0}


def test_box_geometry_none_on_bad_box():
    assert _box_geometry(None) is None
    assert _box_geometry("nope") is None


def test_detections_keep_text_and_geometry():
    result = [
        [[[0, 0], [100, 0], [100, 20], [0, 20]], ("法定停車位", 0.99)],
        [[[110, 0], [140, 0], [140, 20], [110, 20]], ("58", 0.95)],
        [[[0, 0], [50, 0], [50, 20], [0, 20]], ("低信心", 0.30)],  # dropped
    ]
    dets = _detections_from_result(result)
    assert [d["text"] for d in dets] == ["法定停車位", "58"]
    assert dets[0]["x0"] == 0.0 and dets[0]["x1"] == 100.0
    assert dets[1]["yc"] == 10.0


def test_detections_keep_text_when_box_missing():
    # box=None (as in some upstream results) → text kept, geometry absent.
    dets = _detections_from_result([[None, ("純文字", 0.9)]])
    assert dets[0]["text"] == "純文字"
    assert "x0" not in dets[0]
