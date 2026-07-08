# 審議資料表 OCR 能力提升 — 訓練規劃

> 目標：讓系統從「臺北市都市更新審議資料表」（掃描件）穩定抽出結構化欄位，
> 取代目前「PaddleOCR 認字 + 幾何拼表」在密表上的不穩。

## 0. 核心策略：把「固定表格」當武器

這**不是通用文件 AI**，而是「從**一張已知的政府表格**抽 N 個**已知欄位**」。
這個限制反而是最大優勢——不必訓練通用大模型，用「**模板錨定 + 小量微調**」就能
又準又省。而且政府審查**需要可解釋**，所以最終架構是：

```
掃描頁 → [抽取引擎] → 欄位 JSON → [規則/計算驗算(三段式)] → 審查意見
                                    ↑ 這層維持可解釋、可稽核
```

抽取引擎可換（現行幾何重建 → 模板錨定 → 微調 VLM），但**規則驗算層不變**，
確保審查結果始終可解釋。

---

## 1. 兩條軌道（並行）

### Track A — 模板錨定（deterministic，現在就能做，可解釋）
審議資料表版面固定，可「登記一次、對齊每張」：
1. 偵測錨點（表格標題「臺北市都市更新審議資料表」、外框線、區塊標題）。
2. 用 OpenCV homography 把新掃描頁**對齊到標準模板**（去歪斜、對正）。
3. 對每個**已知欄位區域**單獨 OCR（PaddleOCR），只認那一格。
- 優點：deterministic、可解釋、免 GPU、少量樣本即可校準。
- 缺點：換版面（新版審議資料表）要重登記；對齊需要清楚錨點。
- 這條**可立即補上**目前失敗的欄位（如基準容積——位置已知，即使標籤 OCR 沒讀到）。

### Track B — 微調 VLM（最穩健，用你們的資料 + GPU）
把整張表當圖丟給視覺語言模型 → 直接輸出欄位 JSON。
- 模型：**Qwen2.5-VL-7B** 或 **olmOCR-2-7B**（表格/密集文件強）。
- 微調：**LoRA / QLoRA**（固定表格 + 強 base 模型，資料量需求低）。
- 優點：對掃描品質、手寫、勾選框、合併格都原生處理；標註成本最低
  （只標欄位值，不用標 bbox）。
- 缺點：需 GPU；相對「黑盒」——故仍保留規則驗算層做把關。

**建議**：Track A 當現在的橋接；Track B 當落地主力。兩者最終都接到同一套規則驗算層。

---

## 2. 資料準備規格（你正在收集的部分 — 最關鍵）

### 2.1 標註 Schema（image → JSON，低成本）
每張審議資料表掃描頁，標一份欄位 JSON。**只標「值」，不用標 bbox**
（這正好對應你說的「準備多一點筆數」）：

```json
{
  "案名": "擬訂臺北市內湖區東湖段一小段34地號…",
  "送審類別": "B-1",                    // 或文字值「(第1次)審議會版(第1次補正)」
  "基地地號": "內湖區東湖段一小段34地號等1筆",
  "更新單元面積": 804.00,
  "戶數": 20,
  "法定建蔽率": 45.00,
  "法定容積率": 225.00,
  "基準容積": 1809.00,
  "合計獎勵樓地板面積": 1446.79,
  "獎勵比率": 79.98,
  "法定汽車停車位": 33,               // 含無障礙
  "實設汽車停車位_平面": 25,
  "實設汽車停車位_機械": 13,
  "無障礙停車位": 1,
  "充電車位": 0,
  "法定機車停車位": 40,
  "實設機車停車位": 40,
  "填表日期": "113年11月14日",
  "報核日期": "113年5月13日",          // 辦理過程「報核」列
  "實施者": "臺北市內湖區東湖段一小段34地號1筆土地更新單元都市更新會",
  "評價基準日": "112年11月27日"
}
```
> 缺漏欄位一律填 `null`（教模型「讀不到就回 null」，別亂編）。

### 2.2 數量目標（固定表格 + 強 base，需求比通用低很多）
| 用途 | 建議張數 | 說明 |
|---|---|---|
| Track A 模板校準 | 5–10 | 校準欄位區域座標即可 |
| Track B LoRA 起步 | **100–300** | 資料增強後可放大 3–5× |
| 保守驗證（base 夠好時）| 30–50 | 先確認零樣本/少樣本水準 |
| held-out 測試集（gold）| 20–30 | 絕不進訓練，只評分 |

### 2.3 覆蓋「變異」（樣本要選得雜一點才學得動）
- 不同案件、不同承辦填寫習慣。
- **掃描品質**：清晰 / 偏淡 / 歪斜 / 有印章蓋住。
- **送審類別兩型**：勾選框（■B-1）與文字值（審議會版）。
- 版次差異（111 年版為主，若有舊版一併收）。
- 手寫欄位（填表日期常手寫）。

### 2.4 資料增強（把 100 張變 300–500 張）
旋轉 ±2°、亮度/對比抖動、加雜訊、輕微模糊、JPEG 壓縮——模擬真實掃描劣化。

### 2.5 標註工具
- **[PPOCRLabel](https://github.com/PFCCLab/PPOCRLabel)**（`--kie` 模式）：內建 PP-OCR 半自動預標，
  適合 OCR/KIE，標完可直接餵 PaddleOCR 訓練。
- **[Label Studio](https://github.com/HumanSignal/label-studio)**：多型別、有 KIE/文件模板，輸出格式標準，team 協作佳。
- 對 **VLM image→JSON**：其實用 Label Studio 或甚至試算表（檔名 → 欄位值）就夠，
  因為不用標 bbox。

---

## 3. 訓練方法（Track B）

### 3.1 框架（擇一）
- **[LLaMA-Factory](https://github.com/hiyouga/LLaMA-Factory)**：支援 Qwen2.5-VL 的 LoRA/QLoRA/全參數，配置簡單、社群大。
- **[ms-swift](https://github.com/modelscope/ms-swift)**（ModelScope）：對 Qwen 系列 VL 支援最完整。

### 3.2 訓練資料格式（對話式，image + 指令 → JSON）
```
system: 你是審議資料表擷取器，只輸出 JSON，讀不到的欄位填 null。
user:   <image> 請抽出審議資料表的指定欄位。
assistant: {"送審類別":"B-1","基準容積":1809.00, ...}
```

### 3.3 GPU 配方（對應你的時程）
| 硬體 | 可做 | 備註 |
|---|---|---|
| **現在 1 張繪圖卡** | Qwen2.5-VL-7B **零樣本推論 PoC** + **QLoRA 微調**（≥24GB） | 先證明 base 有多準、少量微調能拉多高 |
| **7/20 8×H100** | 7B 全參數微調（1 張就夠，8 張是餘裕）、或上 32B/72B | 對 7B 大材小用；真正瓶頸是資料 |

### 3.4 重要提醒
- **先試零樣本**：Qwen2.5-VL / olmOCR-2 對這張表可能**不微調就已堪用**——
  先跑 PoC，別預設一定要 train。
- **資料才是瓶頸**，不是 GPU。100–300 張標好的表，比第 9 張 H100 有價值。

---

## 4. 評估（沿用你們現有 eval harness 精神）
- **欄位級 precision / recall / F1**：對 held-out gold set，逐欄位比對。
- 數值欄位容許誤差（面積 ±0.5m²、停車位精確）；文字欄位正規化後比對。
- 每次改抽取引擎都跑一次 → 回歸守門（改壞了會被抓到）。
- 參考 `auditor/eval/harness.py` 既有作法，擴成欄位級。

---

## 5. 參考專案（curated）
| 類別 | 專案 | 用途 |
|---|---|---|
| VLM base | [Qwen2.5-VL](https://github.com/QwenLM/Qwen2.5-VL) · [olmOCR-2](https://github.com/allenai/olmocr) | 讀整張表 → JSON |
| VLM 微調 | [LLaMA-Factory](https://github.com/hiyouga/LLaMA-Factory) · [ms-swift](https://github.com/modelscope/ms-swift) | LoRA/QLoRA |
| OCR-free KIE | [Donut](https://github.com/clovaai/donut) | 影像→JSON，較輕量 |
| Layout KIE | [LayoutLMv3](https://github.com/microsoft/unilm/tree/master/layoutlmv3) | 需 word-level 標註 |
| 表格結構 | [Docling](https://github.com/docling-project/docling)(TableFormer) · [PaddleOCR PP-StructureV3](https://github.com/PaddlePaddle/PaddleOCR) | 表格重建 |
| 標註 | [PPOCRLabel](https://github.com/PFCCLab/PPOCRLabel) · [Label Studio](https://github.com/HumanSignal/label-studio) | 半自動標註 |
| 對齊 | OpenCV homography | Track A 模板對齊 |

---

## 6. 分階段時程
1. **現在（無需硬體）**：我建 Track A 模板錨定 PoC + VLM 零樣本 PoC；你這邊開始收集/標註樣本。
2. **收集期**：目標 100–300 張標註表（含 20–30 張 gold）。
3. **7/20 H100 到位**：LoRA 微調 Qwen2.5-VL-7B；用 gold set 評分。
4. **落地**：抽取引擎（微調 VLM 或模板錨定）接進 `enhance_review_table` 當可切換 tier，
   規則驗算層不變。正式服務用一張常駐推論卡（L40S/L4 級即可，不必 H100）。

---

## 7. 我可以馬上產出
- [ ] 審議資料表**標註 schema + 標註指南**（讓你收的樣本直接可訓練）
- [ ] Track A **模板錨定抽取 PoC**（deterministic，現在就能改善基準容積等）
- [ ] Qwen2.5-VL **零樣本推論腳本 + prompt**（你在現有卡上一跑即見水準）
- [ ] LoRA 微調 **LLaMA-Factory 設定檔** + 資料轉換腳本（H100 到位即用）
- [ ] **欄位級 eval harness**（gold set 評分 + 回歸守門）
