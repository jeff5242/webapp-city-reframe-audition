"""把客戶 gold + 300 DPI 圖打包成 Qwen2.5-VL(LLaMA-Factory)訓練包，可直接上 GPU Pod。

產出（放 gitignored 夾，因含圖/gold PII）：
  _訓練包_qwen2vl/
    data/images/*.png              # 各案審議表（冠德用 p13-15 拼接）
    data/train.json                # LLaMA-Factory sharegpt 多模態格式
    data/dataset_info.json         # 資料集註冊
    configs/qwen2_5vl_lora_sft.yaml
    Dockerfile
    run_train.sh
    zeroshot_eval.py               # 零樣本評測（先花小錢看基準）
    README.md

用法：python3 審議資料表_訓練標註/build_bundle.py
"""
import json, os, shutil, glob

ROOT = "/Users/jef/CodeRepository/webapp-city-reframe-audition"
GOLD = "/tmp/gold_all.json"  # 客戶 14 筆彙整（本會話已產）
PNG = f"{ROOT}/審議資料表PDF圖檔轉PNG for OCR訓練/高解析PNG"
OUT = f"{ROOT}/審議資料表PDF圖檔轉PNG for OCR訓練/_訓練包_qwen2vl"

FIELD_KEYS = [
    "案名", "送審類別", "基地地號", "更新單元面積", "戶數", "法定建蔽率", "法定容積率",
    "基準容積", "合計獎勵樓地板面積", "獎勵比率", "法定汽車停車位(含無障礙)",
    "實設汽車停車位_平面", "實設汽車停車位_機械", "實設汽車停車位_無障礙",
    "實設汽車停車位_充電", "法定機車停車位", "實設機車停車位", "填表日期",
    "報核日期", "實施者", "評價基準日",
]

INSTRUCTION = (
    "你是臺北市都市更新審議助手。請仔細閱讀這張「臺北市都市更新審議資料表」，"
    "抽取下列欄位，只輸出 JSON（找不到的欄位填 null，數值保留原格式）：\n"
    + "、".join(FIELD_KEYS)
    + "\n\n重要規則：\n"
      "1. 送審類別：只填「被勾選（打✓或塗黑■）的那一個」選項，"
      "不要把所有選項都列出來（例如只回「B-1：168專案小組版」）。\n"
      "2. 數值用逗號當千分位（如 2,812.00），不要用點。\n"
      "3. 各欄位值以純文字字串輸出，不要巢狀物件。"
)


def stitched_guande():
    m = glob.glob(f"{PNG}/冠德*拼接*300dpi.png")
    return m[0] if m else None


def main():
    gold = json.load(open(GOLD, encoding="utf-8"))
    os.makedirs(f"{OUT}/data/images", exist_ok=True)
    os.makedirs(f"{OUT}/configs", exist_ok=True)

    train, n_img = [], 0
    for s in gold:
        if not s.get("reviewed"):
            continue
        name = s["name"]
        src = f"{PNG}/{name}"
        # 冠德審議表跨 3 頁 → 用拼接圖，資訊才完整
        if name.startswith("冠德"):
            st = stitched_guande()
            if st:
                src = st
        if not os.path.exists(src):
            print(f"  ⚠ 找不到圖：{os.path.basename(src)}，跳過"); continue
        img_rel = f"images/{os.path.basename(src)}"
        shutil.copy(src, f"{OUT}/data/{img_rel}"); n_img += 1
        target = {k: s["fields"].get(k) for k in FIELD_KEYS}
        train.append({
            "messages": [
                {"role": "user", "content": "<image>" + INSTRUCTION},
                {"role": "assistant", "content": json.dumps(target, ensure_ascii=False)},
            ],
            "images": [img_rel],
        })

    json.dump(train, open(f"{OUT}/data/train.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    json.dump({
        "shenyi": {
            "file_name": "train.json",
            "formatting": "sharegpt",
            "columns": {"messages": "messages", "images": "images"},
            "tags": {"role_tag": "role", "content_tag": "content",
                     "user_tag": "user", "assistant_tag": "assistant"},
        }
    }, open(f"{OUT}/data/dataset_info.json", "w", encoding="utf-8"),
        ensure_ascii=False, indent=1)

    _write_static()
    print(f"✓ 訓練包 → {OUT}")
    print(f"  {len(train)} 筆樣本、{n_img} 張圖。冠德已用拼接圖。")
    print("  下一步：把整個 _訓練包_qwen2vl/ 上傳到 Pod（見 README.md）。")


def _write_static():
    dockerfile = '''# Qwen2.5-VL LoRA 微調 — GPU Pod（ixCSP / RTX-5090 Blackwell）
# RTX-5090 = Blackwell(sm_120)，必須用 CUDA 12.8 + PyTorch cu128，cu124 跑不動。
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu22.04
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y python3 python3-pip git && rm -rf /var/lib/apt/lists/*
RUN pip3 install --no-cache-dir "torch>=2.7" "torchvision>=0.22" --index-url https://download.pytorch.org/whl/cu128
RUN git clone --depth 1 https://github.com/hiyouga/LLaMA-Factory.git /app/LLaMA-Factory
WORKDIR /app/LLaMA-Factory
RUN pip3 install --no-cache-dir -e ".[torch,metrics]" && pip3 install --no-cache-dir "transformers>=4.49.0" qwen-vl-utils accelerate
COPY data/ /app/data/
COPY configs/ /app/configs/
COPY run_train.sh zeroshot_eval.py /app/
CMD ["bash", "/app/run_train.sh"]
'''

    yaml = '''### Qwen2.5-VL-7B LoRA SFT（LLaMA-Factory）
model_name_or_path: Qwen/Qwen2.5-VL-7B-Instruct
trust_remote_code: true

stage: sft
do_train: true
finetuning_type: lora
lora_rank: 16
lora_target: all
freeze_vision_tower: true          # 只微調語言端，省顯存、資料少時較穩

dataset: shenyi
dataset_dir: /app/data
template: qwen2_vl
cutoff_len: 4096
overwrite_cache: true
preprocessing_num_workers: 4

output_dir: /app/output/qwen2_5vl_shenyi_lora
logging_steps: 1
save_steps: 50
plot_loss: true
overwrite_output_dir: true

per_device_train_batch_size: 1
gradient_accumulation_steps: 8
learning_rate: 1.0e-4
num_train_epochs: 30.0             # 樣本少 → 多跑幾輪；過擬合就調降
lr_scheduler_type: cosine
warmup_ratio: 0.1
bf16: true
'''

    run = '''#!/bin/bash
set -e
cd /app/LLaMA-Factory
echo "== Qwen2.5-VL LoRA 微調（審議資料表）=="
llamafactory-cli train /app/configs/qwen2_5vl_lora_sft.yaml
echo "== 完成，LoRA 權重在 /app/output/qwen2_5vl_shenyi_lora =="
echo "（記得把 output/ 下載回本機，Pod 關掉就沒了）"
'''

    zeroshot = '''"""零樣本評測：不訓練，直接讓 Qwen2.5-VL 讀圖輸出 JSON，對 gold 算準確率。
先跑這個看「不花錢訓練能到幾分」，再決定要不要 LoRA。
用法（在 Pod 上）：python3 zeroshot_eval.py
"""
import json, re, os, glob
import torch
from transformers import Qwen2_5_VLForConditionalGeneration, AutoProcessor
from qwen_vl_utils import process_vision_info

MODEL = "Qwen/Qwen2.5-VL-7B-Instruct"
# data 夾永遠在腳本旁（bundle 內或 Dockerfile 的 /app），自動抓，避免寫死路徑。
DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
FIELDS = json.load(open(f"{DATA}/train.json", encoding="utf-8"))

model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
    MODEL, torch_dtype=torch.bfloat16, device_map="auto")
proc = AutoProcessor.from_pretrained(MODEL)

def norm(v):
    if v is None: return None
    s = str(v).replace(",", "").replace(" ", "").replace("輛","").replace("戶","").replace("㎡","").replace("m²","").replace("%","")
    if s in ("-","—","–",""): return None
    m = re.match(r"^-?\\d+\\.?\\d*", s); return m.group(0) if m else s

tot=ok=0
for row in FIELDS:
    img = os.path.join(DATA, row["images"][0])
    prompt = row["messages"][0]["content"].replace("<image>","")
    gold = json.loads(row["messages"][1]["content"])
    msgs=[{"role":"user","content":[{"type":"image","image":img},{"type":"text","text":prompt}]}]
    text = proc.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
    imgs,_ = process_vision_info(msgs)
    inp = proc(text=[text], images=imgs, return_tensors="pt").to(model.device)
    out = model.generate(**inp, max_new_tokens=1024)
    gen = proc.batch_decode([o[len(i):] for i,o in zip(inp.input_ids,out)], skip_special_tokens=True)[0]
    try:
        pred = json.loads(re.search(r"\\{.*\\}", gen, re.S).group(0))
    except Exception:
        pred = {}
    for k,gv in gold.items():
        tot+=1
        if norm(pred.get(k))==norm(gv): ok+=1
    print(f"  {os.path.basename(img)[:30]}: 本張命中 {sum(1 for k,gv in gold.items() if norm(pred.get(k))==norm(gv))}/{len(gold)}")
print(f"\\n零樣本欄位準確率：{ok}/{tot} = {100*ok//tot if tot else 0}%")
'''

    readme = '''# 審議資料表 Qwen2.5-VL 訓練包

把整個資料夾上傳到 GPU Pod（RunPod 等）做「圖→JSON 欄位抽取」的評測/微調。
**含真實案件 PII（地號/姓名/印鑑），勿放公開 repo / 公開 Volume。**

## 內容
- `data/images/` — 各案審議資料表 300 DPI（冠德用 p13-15 拼接，資訊完整）
- `data/train.json` — LLaMA-Factory sharegpt 多模態格式（圖 + 指令 + gold JSON）
- `data/dataset_info.json` — 資料集註冊（名稱 `shenyi`）
- `configs/qwen2_5vl_lora_sft.yaml` — LoRA 微調設定
- `Dockerfile` / `run_train.sh` — 自建映像用
- `zeroshot_eval.py` — 零樣本評測（先花小錢看基準）

## 建議順序（省 $500 額度）
1. **先零樣本**：`python3 zeroshot_eval.py` — 不訓練，看 Qwen2.5-VL 直接讀圖能到幾分。
2. 不夠再 **LoRA 微調**：`bash run_train.sh`（樣本少，先把 gold 擴到上百筆更好）。

## 兩種上 Pod 的方式
### A. 用現成範本（最快，不用 build 映像）
1. RunPod 選 **LLaMA-Factory / PyTorch** 範本，掛一個 Network Volume。
2. 把本資料夾傳到 Volume（Pod 檔案瀏覽器 / `runpodctl` / scp）。
3. Pod 內：`pip install -e ".[torch,metrics]"`（若範本沒裝）→ `python3 zeroshot_eval.py` 或 `bash run_train.sh`。

### B. 自建映像（可重現）
```bash
docker build -t <你的帳號>/shenyi-qwen2vl .
docker push <你的帳號>/shenyi-qwen2vl
# RunPod 用這個 image 開 Pod，CMD 會自動跑 run_train.sh
```

## 顯存 / 機器 / 費用
- Qwen2.5-VL-7B LoRA（凍結視覺塔、bf16）：**~18–24GB VRAM**。
  - ixCSP 選 **RTX-5090 24GB 單卡** 剛好夠，或 **獨佔 2×5090(62GB)** 更寬鬆。
  - 12GB 以下不夠（除非改 4-bit QLoRA）。
- **⚠️ RTX-5090 = Blackwell(sm_120)，必須 CUDA 12.8 + PyTorch cu128**（本 Dockerfile 已用）。
  用平台現成範本時，確認 PyTorch 支援 5090（`python3 -c "import torch;print(torch.cuda.is_available(), torch.cuda.get_device_name())"`）。
- 磁碟：環境~10G + 7B模型~16G + 快取~5G ≈ **開 40–50GB**。
- $500 用不完（零樣本幾乎不花錢；LoRA 一輪幾十分鐘）。

## 產出
- LoRA 權重在 `output/qwen2_5vl_shenyi_lora/`。**Pod 關掉就沒了，記得下載回本機。**
- 之後可合併回基模、或地端載 LoRA 推論。
'''

    open(f"{OUT}/Dockerfile", "w").write(dockerfile)
    open(f"{OUT}/configs/qwen2_5vl_lora_sft.yaml", "w").write(yaml)
    open(f"{OUT}/run_train.sh", "w").write(run)
    open(f"{OUT}/zeroshot_eval.py", "w", encoding="utf-8").write(zeroshot)
    open(f"{OUT}/README.md", "w", encoding="utf-8").write(readme)


if __name__ == "__main__":
    main()
