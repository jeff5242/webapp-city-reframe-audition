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


# ── 逗號千分位被 OCR 切開的還原（副總回饋：1,406.00 被讀成 406.00）──────────

def test_comma_split_thousands_are_merged():
    # OCR 把「1,406.00」切成「1,」+「406.00」兩個偵測，需還原成 1406.00
    dets = [
        _det("容積獎勵申請額度", 0, 150, 50),
        _det("1,", 160, 175, 50),
        _det("406.00", 176, 240, 50),
    ]
    assert reconstruct_fields(dets)["bonus_floor_area"] == 1406.00


def test_complete_number_not_merged_with_next_cell():
    # 完整數字不以逗號結尾 → 不併入相鄰的獨立數字格
    dets = [
        _det("法定汽車停車位", 0, 100, 50),
        _det("58", 110, 140, 50),
        _det("108", 300, 340, 50),   # 另一格的獨立數字，不該被併進來
    ]
    assert reconstruct_fields(dets)["legal_parking"] == 58


def test_applied_amount_label_maps_to_bonus_floor_area():
    # 大魯閣審議資料表用「容積獎勵申請額度」字樣，bbox 圖需能對應
    dets = [
        _det("容積獎勵申請額度", 0, 150, 50),
        _det("1,406.00", 160, 260, 50),
    ]
    assert reconstruct_fields(dets)["bonus_floor_area"] == 1406.00


# ── 停車位標籤消歧（大魯閣審議資料表實際用字）────────────────────────────────

def test_legal_parking_matches_han_wuzhangai_label():
    # 大魯閣用「法定(含無障礙)汽車停車位 46輛」——舊關鍵字「法定汽車停車位」抓不到
    dets = [
        _det("法定(含無障礙)汽車停車位", 0, 200, 50),
        _det("46", 210, 240, 50),
    ]
    assert reconstruct_fields(dets)["legal_parking"] == 46


def test_accessible_not_grab_legal_total_row():
    # 「法定(含無障礙)汽車停車位 46」不可被誤判為無障礙(應為法定總數)
    dets = [
        _det("法定(含無障礙)汽車停車位", 0, 200, 50),
        _det("46", 210, 240, 50),
    ]
    out = reconstruct_fields(dets)
    assert out.get("accessible_parking") != 46


def test_accessible_matches_dedicated_row():
    dets = [
        _det("法定無障礙汽車停車位", 0, 200, 50),
        _det("2", 210, 240, 50),
    ]
    assert reconstruct_fields(dets)["accessible_parking"] == 2


def test_accessible_excludes_volume_incentive_item():
    # 「#12無障礙環境設計 84.36㎡」是容積獎勵項，不可被當成無障礙停車位
    dets = [
        _det("#12無障礙環境設計", 0, 200, 50),
        _det("84.36", 210, 260, 50),
    ]
    assert "accessible_parking" not in reconstruct_fields(dets)


def test_base_floor_area_comma_value():
    # 基準容積 2,812.00 —— 逗號合併後應為 2812.0
    dets = [
        _det("基準容積", 0, 100, 50),
        _det("2,", 110, 125, 50),
        _det("812.00", 126, 190, 50),
    ]
    assert reconstruct_fields(dets)["base_floor_area"] == 2812.00


# ── 實設汽車停車位 = 平面 + 機械（審議資料表拆成子列，副總回饋）──────────────

def test_actual_parking_sums_surface_and_mechanical():
    dets = [
        _det("實設汽車停車位（充電0輛）", 0, 150, 100),
        _det("平面", 160, 200, 90), _det("25", 210, 250, 90),
        _det("機械", 160, 200, 140), _det("13", 210, 250, 140),
    ]
    assert reconstruct_fields(dets)["actual_parking"] == 38


def test_actual_parking_no_sum_without_shishe_anchor():
    # 沒有「實設」錨點 → 不把不相關的 平面/機械 當停車位
    dets = [
        _det("平面", 160, 200, 90), _det("25", 210, 250, 90),
        _det("機械", 160, 200, 140), _det("13", 210, 250, 140),
    ]
    assert "actual_parking" not in reconstruct_fields(dets)


def test_actual_parking_no_sum_when_only_one_subrow():
    dets = [
        _det("實設汽車停車位", 0, 150, 100),
        _det("平面", 160, 200, 90), _det("25", 210, 250, 90),
    ]
    # 只有平面、沒有機械 → 不猜總數
    assert reconstruct_fields(dets).get("actual_parking") != 25 or \
        "actual_parking" not in reconstruct_fields(dets)


# ── 送審類別勾選框判讀 ────────────────────────────────────────────────────────

def test_submission_type_from_checked_box():
    dets = [_det("■B-1：168專案小組版", 0, 300, 50)]
    assert reconstruct_fields(dets)["submission_type"] == "B-1"


def test_submission_type_checked_by_description():
    dets = [_det("☑ 審議會版", 0, 200, 50)]
    assert reconstruct_fields(dets)["submission_type"] == "C"


def test_submission_type_none_when_no_filled_box():
    # 全是空框 □ → 不判定（維持人工確認）
    dets = [
        _det("□A-1：送件版", 0, 200, 50),
        _det("□B-1：168專案小組版", 0, 200, 80),
    ]
    assert "submission_type" not in reconstruct_fields(dets)


def test_submission_type_picks_only_the_filled_one():
    dets = [
        _det("□A-1：送件版", 0, 200, 50),
        _det("■B-2：幹事會複審版", 0, 200, 80),
        _det("□C：審議會版", 0, 200, 110),
    ]
    assert reconstruct_fields(dets)["submission_type"] == "B-2"
