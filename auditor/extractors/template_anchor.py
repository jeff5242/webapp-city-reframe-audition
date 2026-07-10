"""模板錨定擷取（Track A tier）：審議資料表固定版面，裁切「已知欄位區域」單獨 OCR，
補回全頁 OCR 抓不到的欄位（主要是基準容積）。確定性、可解釋（政府審查適用）。

安全機制（避免不同版面誤填）：模板抓的「合計獎勵樓地板面積」需與主流程已知值相符，
才視為「模板對準了此頁版面」，此時才採信同版面的基準容積。版面不同 → 對不上 → 不填
（純補值、絕不覆寫、絕不亂猜）。
"""
from __future__ import annotations

import re
from typing import Optional

# 正規化欄位「值格」區域 (x0,y0,x1,y1)，依標準「臺北市都市更新審議資料表」版面標定
# （由 300 DPI 大魯閣事業計畫圖量得）。不同版次版面若不同，對齊驗證會自動略過。
TEMPLATE_REGIONS = {
    "base_floor_area":  (0.80, 0.095, 0.93, 0.138),  # 基準容積（右上）
    "bonus_floor_area": (0.80, 0.712, 0.93, 0.748),  # 合計獎勵樓地板面積（對齊驗證用）
}

_NUM_RE = re.compile(r"\d[\d,，]*(?:\.\d+)?")


def _num(text: Optional[str]) -> Optional[float]:
    if not text:
        return None
    # 去空白：小塊 OCR 常把「2,812」讀成「2 812」，去空白才不會被切斷。
    m = _NUM_RE.search(re.sub(r"\s+", "", text))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", "").replace("，", ""))
    except ValueError:
        return None


def _aligned(tmpl_bonus: Optional[float], known_bonus: Optional[float]) -> bool:
    """模板抓的獎勵樓地板 == 主流程已知值（±1% 或 ±1）→ 模板對準此頁版面。"""
    if not tmpl_bonus or not known_bonus:
        return False
    return abs(tmpl_bonus - known_bonus) <= max(1.0, known_bonus * 0.01)


def extract_base_floor_area(pdf_path: str, page_num: int,
                            known_bonus_floor_area: Optional[float]) -> dict:
    """回傳 {'base_floor_area': 值}，僅在模板對齊驗證通過且值合理時；否則 {}。

    known_bonus_floor_area：主流程（幾何重建）已抓到的合計獎勵樓地板面積，作為對齊錨。
    """
    if not known_bonus_floor_area:
        return {}
    try:
        from ..parsers.ocr_reader import ocr_regions
    except Exception:
        return {}
    texts = ocr_regions(pdf_path, page_num - 1, TEMPLATE_REGIONS, zoom=4.0)
    if not texts:
        return {}
    if not _aligned(_num(texts.get("bonus_floor_area")), known_bonus_floor_area):
        return {}
    base = _num(texts.get("base_floor_area"))
    # 合理性：基準容積必大於獎勵樓地板面積，且量級合理。
    if base and known_bonus_floor_area < base <= known_bonus_floor_area * 20 and base <= 1_000_000:
        return {"base_floor_area": base}
    return {}
