"""高解析審議資料表 → 標註骨架（新命名版）。

與 make_annotations.py 差異：
  1. 讀 高解析PNG/（300 DPI 重輸出），非低解析原圖。
  2. 檔名採新規則 `案名_文件類型_版本_p頁_300dpi.png` → 直接切 token 取 meta，
     不再靠【】與關鍵字白名單（涵蓋所有案）。
  3. 輸出到 高解析標註/ 子夾，不覆蓋已提交的低解析骨架與 /label 工具。

OCR 引擎：優先 PaddleOCR（prod、reconstruct_fields 對它調校），無則退回 RapidOCR。
"""
import json, os, re, sys, time

ROOT = "/Users/jef/CodeRepository/webapp-city-reframe-audition"
sys.path.insert(0, ROOT)

SRC = os.path.join(ROOT, "審議資料表PDF圖檔轉PNG for OCR訓練", "高解析PNG")
OUT = os.path.join(ROOT, "審議資料表_訓練標註", "高解析標註")
os.makedirs(OUT, exist_ok=True)

FIELD_KEYS = [
    "案名", "送審類別", "基地地號",
    "更新單元面積", "戶數", "法定建蔽率", "法定容積率",
    "基準容積", "合計獎勵樓地板面積", "獎勵比率",
    "法定汽車停車位(含無障礙)",
    "實設汽車停車位_平面", "實設汽車停車位_機械",
    "實設汽車停車位_無障礙", "實設汽車停車位_充電",
    "法定機車停車位", "實設機車停車位",
    "填表日期", "報核日期", "實施者", "評價基準日",
]

RECON_MAP = {
    "base_floor_area": "基準容積",
    "bonus_floor_area": "合計獎勵樓地板面積",
    "legal_parking": "法定汽車停車位(含無障礙)",
    "accessible_parking": "實設汽車停車位_無障礙",
    "ev_parking": "實設汽車停車位_充電",
    "submission_type": "送審類別",
}


def parse_meta(fname: str) -> dict:
    """新命名 `案名_文件類型_版本_p頁_300dpi`：以底線切 token 取 meta。"""
    base = os.path.splitext(fname)[0]
    parts = base.split("_")
    case = parts[0] if parts else None
    if "權利變換" in base or "權變" in base:
        doc = "權利變換計畫"
    elif "事業計畫" in base or "事業" in base:
        doc = "事業計畫"
    else:
        doc = "未定"
    version = None
    for v in ["168專案小組", "168專案會複審", "專案審查", "幹事複審補正",
              "幹事複審", "幹事會", "審議會版", "複審", "核定版", "公開展覽"]:
        if v in base:
            version = v
            break
    m = re.search(r"[pP](\d+)", base)
    page = int(m.group(1)) if m else None
    return {"case": case, "doc_type": doc, "version": version,
            "補正": "補正" in base, "page": page}


def _make_ocr():
    """回傳 (engine_name, callable(img_path)->dets)。優先 PaddleOCR。"""
    try:
        from auditor.parsers.ocr_reader import ocr_available
        if ocr_available():
            from paddleocr import PaddleOCR  # noqa
            from auditor.parsers.ocr_reader import _get_reader, _detections_from_result
            import numpy as np
            from PIL import Image

            def run(path):
                reader = _get_reader()
                arr = np.array(Image.open(path).convert("RGB"))
                res = reader.ocr(arr) if hasattr(reader, "ocr") else reader(arr)
                return _detections_from_result(res)
            return "paddleocr", run
    except Exception:
        pass
    # 退回 RapidOCR
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        from rapidocr import RapidOCR
    ocr = RapidOCR()

    def run(path):
        res = ocr(path)
        items = res[0] if isinstance(res, tuple) else res
        dets = []
        for it in (items or []):
            box, text, conf = it[0], it[1], it[2]
            xs = [p[0] for p in box]; ys = [p[1] for p in box]
            dets.append({"text": text, "conf": float(conf),
                         "x0": min(xs), "x1": max(xs),
                         "yc": (min(ys) + max(ys)) / 2, "h": max(ys) - min(ys)})
        return dets
    return "rapidocr", run


def prefill(dets):
    from auditor.extractors.table_reconstruct import reconstruct_fields
    try:
        out = reconstruct_fields(dets)
    except Exception:
        return {}
    filled = {}
    for rk, sk in RECON_MAP.items():
        if out.get(rk) is not None:
            filled[sk] = out[rk]
    detail = out.get("actual_parking_detail")
    if detail:
        m1 = re.search(r"平面(\d+)", detail); m2 = re.search(r"機械(\d+)", detail)
        if m1: filled["實設汽車停車位_平面"] = int(m1.group(1))
        if m2: filled["實設汽車停車位_機械"] = int(m2.group(1))
    return filled


def text_reference(dets):
    lines = sorted(dets, key=lambda d: (round(d["yc"] / 20), d["x0"]))
    return [d["text"] for d in lines if (d.get("text") or "").strip()]


def main():
    pngs = sorted(os.path.join(SRC, f) for f in os.listdir(SRC)
                  if f.lower().endswith(".png"))
    if not pngs:
        print(f"找不到高解析 PNG：{SRC}", file=sys.stderr)
        return 2
    engine, run_ocr = _make_ocr()
    print(f"OCR 引擎：{engine}｜共 {len(pngs)} 張\n")

    manifest = []
    for i, path in enumerate(pngs, 1):
        fname = os.path.basename(path)
        meta = parse_meta(fname)
        t0 = time.time()
        try:
            dets = run_ocr(path)
        except Exception as e:
            print(f"  [{i}] {fname[:40]}… OCR 失敗：{e}", flush=True)
            dets = []
        auto = prefill(dets)
        ref = text_reference(dets)
        print(f"  [{i}/{len(pngs)}] {fname[:44]}… OCR={len(dets)} 預填={len(auto)} ({time.time()-t0:.0f}s)", flush=True)

        fields = {k: auto.get(k) for k in FIELD_KEYS}
        ann = {
            "image": os.path.join("高解析PNG", fname),
            "meta": meta,
            "fields": fields,
            "_note": "請對照原圖檢查/修正 fields；null=OCR 未自動填、需人工填。",
            "_ocr_engine": engine,
            "_ocr_text_reference": ref,
        }
        out_name = f"{i:02d}_" + re.sub(r"[^\w一-鿿]+", "_", os.path.splitext(fname)[0])[:60] + ".json"
        with open(os.path.join(OUT, out_name), "w", encoding="utf-8") as fp:
            json.dump(ann, fp, ensure_ascii=False, indent=2)
        manifest.append({"id": i, "annotation": out_name, "image": ann["image"],
                         **meta, "auto_filled": len(auto)})

    with open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8") as fp:
        json.dump({"count": len(manifest), "source": "高解析PNG (300 DPI)",
                   "ocr_engine": engine, "field_keys": FIELD_KEYS,
                   "samples": manifest}, fp, ensure_ascii=False, indent=2)
    print(f"\n✓ 輸出 {len(manifest)} 份標註 + manifest.json → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
