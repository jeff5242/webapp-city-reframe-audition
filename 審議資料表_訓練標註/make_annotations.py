"""把審議資料表訓練 PNG 建成標註骨架：manifest + 每張欄位 JSON（OCR 半自動預填）。"""
import json, os, re, sys, time
sys.path.insert(0, "/Users/jef/CodeRepository/webapp-city-reframe-audition")

SRC = "/Users/jef/CodeRepository/webapp-city-reframe-audition/審議資料表PDF圖檔轉PNG for OCR訓練"
OUT = "/Users/jef/CodeRepository/webapp-city-reframe-audition/審議資料表_訓練標註"
os.makedirs(OUT, exist_ok=True)

# 標註 schema：系統實際用到的關鍵欄位（值型；null=待填）
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

# reconstruct_fields 的欄位 → schema 欄位（自動預填用）
RECON_MAP = {
    "base_floor_area": "基準容積",
    "bonus_floor_area": "合計獎勵樓地板面積",
    "legal_parking": "法定汽車停車位(含無障礙)",
    "accessible_parking": "實設汽車停車位_無障礙",
    "ev_parking": "實設汽車停車位_充電",
    "submission_type": "送審類別",
}


def parse_meta(fname: str) -> dict:
    base = os.path.splitext(fname)[0]
    if "權利變換" in base or "權變" in base:
        doc = "權利變換計畫"
    elif "事業計畫" in base:
        doc = "事業計畫"
    else:
        doc = "未定"
    version = None
    for v in ["168專案小組", "168專案會複審", "專案審查", "幹事複審", "幹事會", "審議會版", "複審", "核定版", "公開展覽"]:
        if v in base:
            version = v
            break
    补 = "補正" if "補正" in base else None
    m = re.search(r"[pP](\d+)", base)
    page = int(m.group(1)) if m else None
    # 案名：抓中括號或前綴關鍵字
    case = None
    mb = re.search(r"【(.+?)】", base)
    if mb:
        case = mb.group(1)
    else:
        for kw in ["大魯閣", "開明段", "興隆段", "東湖段", "騰竣碧湖", "冠德"]:
            if kw in base:
                case = kw
                break
    return {"case": case, "doc_type": doc, "version": version,
            "補正": bool(补), "page": page}


def ocr_detections(img_path):
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        from rapidocr import RapidOCR
    ocr = RapidOCR()
    res = ocr(img_path)
    items = res[0] if isinstance(res, tuple) else res
    dets = []
    for it in (items or []):
        box, text, conf = it[0], it[1], it[2]
        xs = [p[0] for p in box]; ys = [p[1] for p in box]
        dets.append({"text": text, "conf": float(conf),
                     "x0": min(xs), "x1": max(xs),
                     "yc": (min(ys) + max(ys)) / 2, "h": max(ys) - min(ys)})
    return dets


def prefill(dets):
    from auditor.extractors.table_reconstruct import reconstruct_fields
    out = reconstruct_fields(dets)
    filled = {}
    for rk, sk in RECON_MAP.items():
        if out.get(rk) is not None:
            filled[sk] = out[rk]
    # 實設平面/機械來自明細字串
    detail = out.get("actual_parking_detail")
    if detail:
        m1 = re.search(r"平面(\d+)", detail); m2 = re.search(r"機械(\d+)", detail)
        if m1: filled["實設汽車停車位_平面"] = int(m1.group(1))
        if m2: filled["實設汽車停車位_機械"] = int(m2.group(1))
    return filled


def text_reference(dets):
    """依 (列, 欄) 位置排序的 OCR 文字，供標註者對照原圖快速填值。"""
    lines = sorted(dets, key=lambda d: (round(d["yc"] / 20), d["x0"]))
    return [d["text"] for d in lines if (d.get("text") or "").strip()]


def main():
    pngs = []
    for root, _, files in os.walk(SRC):
        for f in files:
            if f.lower().endswith(".png"):
                pngs.append(os.path.join(root, f))
    pngs.sort()

    manifest = []
    for i, path in enumerate(pngs, 1):
        rel = os.path.relpath(path, SRC)
        fname = os.path.basename(path)
        group = rel.split(os.sep)[0]
        meta = parse_meta(fname)
        t0 = time.time()
        dets = ocr_detections(path)
        auto = prefill(dets)
        ref = text_reference(dets)
        print(f"  [{i}/{len(pngs)}] {fname[:40]}… OCR={len(dets)} 預填={len(auto)} ({time.time()-t0:.0f}s)", flush=True)

        fields = {k: auto.get(k) for k in FIELD_KEYS}
        ann = {
            "image": rel,
            "group": group,
            "meta": meta,
            "fields": fields,
            "_note": "請對照原圖檢查/修正 fields；null 表示 OCR 未自動填、需人工填。",
            "_ocr_text_reference": ref,
        }
        out_name = f"{i:02d}_" + re.sub(r"[^\w一-鿿]+", "_", os.path.splitext(fname)[0])[:50] + ".json"
        with open(os.path.join(OUT, out_name), "w", encoding="utf-8") as fp:
            json.dump(ann, fp, ensure_ascii=False, indent=2)
        manifest.append({"id": i, "annotation": out_name, "image": rel,
                         "group": group, **meta, "auto_filled": len(auto)})

    with open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8") as fp:
        json.dump({"count": len(manifest), "field_keys": FIELD_KEYS,
                   "samples": manifest}, fp, ensure_ascii=False, indent=2)
    print(f"\n✓ 輸出 {len(manifest)} 份標註 + manifest.json → {OUT}")


if __name__ == "__main__":
    main()
