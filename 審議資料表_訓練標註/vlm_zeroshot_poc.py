"""審議資料表 VLM 零樣本抽取 PoC。

在地端 GPU 上用 Qwen2.5-VL（或相容 VLM）「看圖 → 輸出欄位 JSON」，不微調，
看 base 模型對這張表能抓多準——決定要不要投入 LoRA 微調（等 H100）。

安裝（在有 GPU 的機器）:
    pip install "transformers>=4.49" qwen-vl-utils accelerate torch pillow
    # 顯存不足時 4-bit 量化: pip install bitsandbytes  然後加 --load-4bit

跑法:
    # 7B（約需 18-24GB 顯存；不足改 --model Qwen/Qwen2.5-VL-3B-Instruct 或 --load-4bit）
    python vlm_zeroshot_poc.py --images-dir "../審議資料表PDF圖檔轉PNG for OCR訓練" --out vlm_out.json
    # 有 gold 標註時順便評分（欄位級準確率）:
    python vlm_zeroshot_poc.py --images-dir ... --gold 審議資料表_標註_已修正.json --out vlm_out.json

輸出: 每張圖的抽取 JSON；有 gold 則附欄位級 precision（抽對/有值）。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time

# 與標註 schema 一致（manifest.json 的 field_keys）
FIELDS = [
    "案名", "送審類別", "基地地號", "更新單元面積", "戶數", "法定建蔽率", "法定容積率",
    "基準容積", "合計獎勵樓地板面積", "獎勵比率", "法定汽車停車位(含無障礙)",
    "實設汽車停車位_平面", "實設汽車停車位_機械", "實設汽車停車位_無障礙",
    "實設汽車停車位_充電", "法定機車停車位", "實設機車停車位",
    "填表日期", "報核日期", "實施者", "評價基準日",
]

PROMPT = (
    "這是一張「臺北市都市更新審議資料表」的掃描圖。請逐格判讀，抽出下列欄位，"
    "只輸出 JSON、不要多餘文字。讀不到或表上空白的欄位一律填 null（不要臆測）。"
    "數值含小數與千分位照原表；停車位為整數；日期用民國格式（如 113年5月13日）。\n"
    "欄位：" + "、".join(FIELDS) + "\n"
    'JSON 格式範例：{"送審類別":"B-1","基準容積":1809.00,"合計獎勵樓地板面積":1446.79,'
    '"法定汽車停車位(含無障礙)":33,"實設汽車停車位_平面":25,"實設汽車停車位_機械":13,'
    '"填表日期":"113年11月14日","報核日期":"113年5月13日", ... 其餘欄位 ...}'
)


def load_model(model_id: str, load_4bit: bool):
    import torch
    from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration

    kwargs = {"torch_dtype": "auto", "device_map": "auto"}
    if load_4bit:
        from transformers import BitsAndBytesConfig

        kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_compute_dtype=torch.float16,
        )
    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(model_id, **kwargs)
    processor = AutoProcessor.from_pretrained(model_id)
    return model, processor


def extract_one(model, processor, img_path: str, max_new_tokens: int = 1024) -> dict:
    from qwen_vl_utils import process_vision_info

    messages = [{
        "role": "user",
        "content": [
            {"type": "image", "image": img_path},
            {"type": "text", "text": PROMPT},
        ],
    }]
    text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    image_inputs, video_inputs = process_vision_info(messages)
    inputs = processor(text=[text], images=image_inputs, videos=video_inputs,
                       padding=True, return_tensors="pt").to(model.device)
    gen = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
    trimmed = gen[:, inputs.input_ids.shape[1]:]
    out = processor.batch_decode(trimmed, skip_special_tokens=True)[0]
    return _parse_json(out)


def _parse_json(text: str) -> dict:
    """從模型輸出取 JSON（容忍 ```json fences 與前後雜訊）。"""
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {"_raw": text, "_parse_error": True}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {"_raw": text, "_parse_error": True}


def _norm(v):
    if v is None:
        return None
    s = str(v).strip()
    return re.sub(r"[,\s㎡m²輛%]", "", s) or None


def score(pred: dict, gold: dict) -> tuple:
    """欄位級：以 gold 有值的欄位為分母，pred 正規化後相符為分子。"""
    hit = total = 0
    misses = []
    for k, gv in (gold.get("fields") or gold).items():
        if gv in (None, ""):
            continue
        total += 1
        if _norm(pred.get(k)) == _norm(gv):
            hit += 1
        else:
            misses.append(f"{k}: 期望 {gv!r} 得到 {pred.get(k)!r}")
    return hit, total, misses


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--images-dir", required=True)
    ap.add_argument("--model", default="Qwen/Qwen2.5-VL-7B-Instruct")
    ap.add_argument("--load-4bit", action="store_true")
    ap.add_argument("--gold", default=None, help="已修正標註 JSON（陣列），用來評分")
    ap.add_argument("--out", default="vlm_out.json")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 張（測試用）")
    args = ap.parse_args()

    imgs = sorted(glob.glob(os.path.join(args.images_dir, "**", "*.png"), recursive=True))
    if args.limit:
        imgs = imgs[: args.limit]
    if not imgs:
        print("找不到 PNG", file=sys.stderr)
        return 2

    gold_by_name = {}
    if args.gold and os.path.exists(args.gold):
        for g in json.load(open(args.gold, encoding="utf-8")):
            gold_by_name[g.get("name") or os.path.basename(g.get("image", ""))] = g

    print(f"載入模型 {args.model} …", flush=True)
    model, processor = load_model(args.model, args.load_4bit)

    results, tot_hit, tot_all = [], 0, 0
    for i, path in enumerate(imgs, 1):
        name = os.path.basename(path)
        t0 = time.time()
        pred = extract_one(model, processor, path)
        rec = {"image": name, "pred": pred, "seconds": round(time.time() - t0, 1)}
        if name in gold_by_name:
            hit, total, misses = score(pred, gold_by_name[name])
            rec["score"] = {"hit": hit, "total": total, "misses": misses}
            tot_hit += hit; tot_all += total
            print(f"  [{i}/{len(imgs)}] {name[:36]}… {hit}/{total} 對 ({rec['seconds']}s)", flush=True)
        else:
            filled = sum(1 for v in pred.values() if v not in (None, "") and not str(v).startswith("_"))
            print(f"  [{i}/{len(imgs)}] {name[:36]}… 抽出 {filled} 欄 ({rec['seconds']}s)", flush=True)
        results.append(rec)

    json.dump(results, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n✓ 輸出 → {args.out}")
    if tot_all:
        print(f"★ 零樣本欄位級準確率：{tot_hit}/{tot_all} = {tot_hit / tot_all:.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
