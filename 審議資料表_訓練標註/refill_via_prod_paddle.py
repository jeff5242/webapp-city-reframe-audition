"""用 prod 的 PaddleOCR（/debug/ocr）重跑 14 張高解析標註的自動填。

本機只有 RapidOCR（拼不回欄位），prod 有 PaddleOCR + reconstruct_fields 調校。
流程：抽單頁 PDF → POST /debug/ocr(zoom5) → reconstruct_fields → 回寫高解析標註 JSON。
"""
import json, os, re, subprocess, sys, tempfile

ROOT = "/Users/jef/CodeRepository/webapp-city-reframe-audition"
sys.path.insert(0, ROOT)
import fitz
from auditor.extractors.table_reconstruct import reconstruct_fields

ANN = f"{ROOT}/審議資料表_訓練標註/高解析標註"
PDFDIR = f"{ROOT}/事業計劃報告書及權利變更計劃書"
URL = "https://urban-renewal.sakilu-dev.uk/debug/ocr"
ZOOM = "5"

RECON_MAP = {
    "base_floor_area": "基準容積",
    "bonus_floor_area": "合計獎勵樓地板面積",
    "legal_parking": "法定汽車停車位(含無障礙)",
    "accessible_parking": "實設汽車停車位_無障礙",
    "ev_parking": "實設汽車停車位_充電",
    "submission_type": "送審類別",
}


def pdf_and_page(img_name: str):
    """'大魯閣_事業計畫_1150206_p19_300dpi.png' → ('大魯閣_事業計畫_1150206.pdf', 19)."""
    m = re.search(r"_p(\d+)_300dpi", img_name)
    page = int(m.group(1))
    stem = img_name[: m.start()]
    return f"{stem}.pdf", page


def ocr_via_prod(pdf_path: str, page: int, tmpdir: str):
    """抽單頁→POST /debug/ocr→回傳 detections。"""
    d = fitz.open(pdf_path)
    nd = fitz.open(); nd.insert_pdf(d, from_page=page - 1, to_page=page - 1)
    one = os.path.join(tmpdir, "page.pdf"); nd.save(one); nd.close(); d.close()
    out = os.path.join(tmpdir, "resp.json")
    r = subprocess.run(
        ["curl", "-s", "-m", "200", "-X", "POST", URL,
         "-F", f"pdf=@{one}", "-F", "page=1", "-F", f"zoom={ZOOM}",
         "-o", out, "-w", "%{http_code}"],
        capture_output=True, text=True)
    if r.stdout.strip() != "200":
        raise RuntimeError(f"HTTP {r.stdout.strip()}")
    return json.load(open(out))["detections"]


def prefill(dets):
    out = reconstruct_fields(dets)
    filled = {RECON_MAP[k]: v for k, v in out.items()
              if k in RECON_MAP and v is not None}
    detail = out.get("actual_parking_detail")
    if detail:
        m1 = re.search(r"平面(\d+)", detail); m2 = re.search(r"機械(\d+)", detail)
        if m1: filled["實設汽車停車位_平面"] = int(m1.group(1))
        if m2: filled["實設汽車停車位_機械"] = int(m2.group(1))
    return filled, out.get("actual_parking_detail")


def text_reference(dets):
    lines = sorted(dets, key=lambda d: (round(d["yc"] / 20), d["x0"]))
    return [d["text"] for d in lines if (d.get("text") or "").strip()]


def main():
    manifest = json.load(open(f"{ANN}/manifest.json", encoding="utf-8"))
    total = 0
    for s in manifest["samples"]:
        ann_path = f"{ANN}/{s['annotation']}"
        ann = json.load(open(ann_path, encoding="utf-8"))
        img_name = os.path.basename(ann["image"])
        pdf_name, page = pdf_and_page(img_name)
        pdf_path = f"{PDFDIR}/{pdf_name}"
        if not os.path.exists(pdf_path):
            print(f"  ✗ 找不到 PDF：{pdf_name}", flush=True); continue
        try:
            with tempfile.TemporaryDirectory() as td:
                dets = ocr_via_prod(pdf_path, page, td)
        except Exception as e:
            print(f"  ✗ {img_name[:40]} OCR失敗：{e}", flush=True); continue
        filled, detail = prefill(dets)
        # 回寫：只覆蓋有值的欄，其餘保持 null
        for k in ann["fields"]:
            ann["fields"][k] = filled.get(k, None)
        ann["_ocr_engine"] = "paddleocr(prod /debug/ocr zoom5)"
        ann["_ocr_text_reference"] = text_reference(dets)
        if detail:
            ann["_actual_parking_detail"] = detail
        json.dump(ann, open(ann_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        s["auto_filled"] = len(filled)
        total += len(filled)
        print(f"  ✓ {s['id']:2} {img_name[:40]}  OCR={len(dets)} 預填={len(filled)} {list(filled.keys())}", flush=True)
    manifest["ocr_engine"] = "paddleocr(prod /debug/ocr zoom5)"
    json.dump(manifest, open(f"{ANN}/manifest.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n✓ 14 張重填完成，共自動填 {total} 格（PaddleOCR）", flush=True)


if __name__ == "__main__":
    main()
