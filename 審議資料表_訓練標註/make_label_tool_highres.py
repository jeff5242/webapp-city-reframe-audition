"""高解析版標註工具產生器：讀 高解析標註/ + 高解析PNG/，嵌入圖降到 max 2200px 寬。

與 make_label_tool.py 差異：資料源改高解析、嵌入前 downscale（4960px 全嵌會讓 HTML 破 40MB）。
HTML 模板/前端邏輯完全沿用 make_label_tool.py，只換資料與縮圖。
"""
import base64, io, json, os
from PIL import Image

ROOT = "/Users/jef/CodeRepository/webapp-city-reframe-audition"
ANN = f"{ROOT}/審議資料表_訓練標註/高解析標註"
PNG = f"{ROOT}/審議資料表PDF圖檔轉PNG for OCR訓練"
OUT = f"{ROOT}/審議資料表_訓練標註/標註工具.html"   # 覆蓋 /label 服務的檔（已 gitignore）
EMBED_MAX_W = 2200  # 嵌入寬度上限（前端還能再放大）

# 直接沿用 make_label_tool.py 的 HTML 模板，避免重寫
import importlib.util
_spec_path = f"{ROOT}/審議資料表_訓練標註/make_label_tool.py"


def img_data_uri(rel_path: str) -> str:
    im = Image.open(f"{PNG}/{rel_path}").convert("RGB")
    if im.width > EMBED_MAX_W:
        h = round(im.height * EMBED_MAX_W / im.width)
        im = im.resize((EMBED_MAX_W, h), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def main():
    manifest = json.load(open(f"{ANN}/manifest.json", encoding="utf-8"))
    FIELDS = manifest["field_keys"]
    samples = []
    for s in manifest["samples"]:
        ann = json.load(open(f"{ANN}/{s['annotation']}", encoding="utf-8"))
        samples.append({
            "id": s["id"],
            "name": os.path.basename(ann["image"]),
            "meta": ann["meta"],
            "group": "",
            "fields": {k: ann["fields"].get(k) for k in FIELDS},
            "ocr": ann.get("_ocr_text_reference", []),
            "img": img_data_uri(ann["image"]),
        })
        print(f"  embedded {s['id']:>2}. {samples[-1]['name'][:44]}", flush=True)

    # 取用 make_label_tool.py 的 HTML 模板字串（其 __main__ 不執行，故直接讀原始碼取 HTML 常數）
    src = open(_spec_path, encoding="utf-8").read()
    marker = 'HTML = """'
    start = src.index(marker) + len(marker)
    end = src.index('"""', start)
    HTML = src[start:end]

    html = (HTML.replace("__DATA__", json.dumps(samples, ensure_ascii=False))
                .replace("__FIELDS__", json.dumps(FIELDS, ensure_ascii=False))
                .replace("__N__", str(len(samples))))
    open(OUT, "w", encoding="utf-8").write(html)
    print(f"\n✓ 高解析標註工具 → {OUT}  ({len(html)/1024/1024:.1f} MB, {len(samples)} 張)")


if __name__ == "__main__":
    main()
