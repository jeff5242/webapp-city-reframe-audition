"""模板錨定擷取 PoC（Track A）：審議資料表是固定版面，用「已知欄位區域」裁切後
單獨 OCR，即使全頁 OCR 讀不到標籤也能取到值。確定性、可解釋（政府審查適用）。

核心：不靠「找標籤→取鄰值」（易被密表打亂），而是「我知道基準容積在版面右上角那一格」
→ 裁那一格 → 只 OCR 那一小塊（乾淨、準）。並可衍生：容積獎勵上限 = 基準容積 × 50%。

限制：目前用固定正規化座標（同一版面、掃描完整時可用）；正式版應加「錨點對齊
（偵測表格外框/標題/QR → homography 校正）」以容忍掃描位移與歪斜。

用法：
    python template_extract_poc.py <高解析審議資料表.png> [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys

# 正規化欄位「值格」區域 (x0,y0,x1,y1)，依「臺北市都市更新審議資料表」標準版面標定。
# 座標由大魯閣 300 DPI 圖量得；不同版次（幹事會版等）版面若不同需另建模板。
TEMPLATE_REGIONS = {
    "base_floor_area":  (0.80, 0.095, 0.93, 0.138),  # 基準容積（右上）
    "bonus_floor_area": (0.80, 0.712, 0.93, 0.748),  # 合計獎勵樓地板面積
    "legal_parking":    (0.252, 0.548, 0.305, 0.578),  # 法定(含無障礙)汽車停車位（值格）
}
_FLOAT = {"base_floor_area", "bonus_floor_area"}
_LABELS = {"base_floor_area": "基準容積", "bonus_floor_area": "合計獎勵樓地板面積",
           "legal_parking": "法定(含無障礙)汽車停車位", "bonus_limit_derived": "容積獎勵上限（衍生）"}

_NUM_RE = re.compile(r"\d[\d,，]*(?:\.\d+)?")


def _num(text: str, as_int: bool):
    # 去空白：裁切後小塊 OCR 常把「2,812」讀成「2 812」，去空白才不會被切斷。
    text = re.sub(r"\s+", "", text or "")
    m = _NUM_RE.search(text)
    if not m:
        return None
    s = m.group(0).replace(",", "").replace("，", "")
    try:
        return int(float(s)) if as_int else float(s)
    except ValueError:
        return None


def _make_ocr():
    try:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError:
            from rapidocr import RapidOCR
        return RapidOCR()
    except Exception as e:
        print(f"需安裝 rapidocr：pip install rapidocr_onnxruntime  ({e})", file=sys.stderr)
        raise


def extract_by_template(img_path: str, ocr=None) -> dict:
    import numpy as np
    from PIL import Image

    im = Image.open(img_path).convert("RGB")
    W, H = im.size
    ocr = ocr or _make_ocr()
    out = {}
    for field, (x0, y0, x1, y1) in TEMPLATE_REGIONS.items():
        crop = im.crop((int(x0 * W), int(y0 * H), int(x1 * W), int(y1 * H)))
        res = ocr(np.array(crop))
        items = res[0] if isinstance(res, tuple) else res
        text = " ".join((it[1] or "") for it in (items or []))
        out[field] = _num(text, as_int=(field not in _FLOAT))
    # 衍生：容積獎勵上限 = 基準容積 × 50%（都更條例§65）——救回原本無欄位的上限
    if out.get("base_floor_area"):
        out["bonus_limit_derived"] = round(out["base_floor_area"] * 0.5, 2)
    return out


def main():
    ap = argparse.ArgumentParser(description="模板錨定擷取 PoC（審議資料表固定版面）")
    ap.add_argument("image", help="高解析審議資料表 PNG")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    res = extract_by_template(args.image)
    print("── 模板錨定擷取結果 ──")
    for k in ["base_floor_area", "bonus_floor_area", "legal_parking", "bonus_limit_derived"]:
        print(f"  {_LABELS.get(k, k)}: {res.get(k)}")
    # CALC-001/004 可行性
    b, up = res.get("bonus_floor_area"), res.get("bonus_limit_derived")
    if b is not None and up is not None:
        ok = "✓ 通過" if b <= up + 0.1 else "✗ 超限"
        print(f"  → CALC-001 合計獎勵 {b} ≤ 上限 {up}? {ok}")
    if args.json:
        json.dump(res, open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
