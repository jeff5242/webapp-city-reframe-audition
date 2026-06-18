# 臺北市都市更新審議自動審查系統 POC

根據 **111年3月24日修正公布版**規章，自動審查都市更新事業計畫書及權利變換計畫書，快速找出不符合規範的項目。

## 快速開始

### 1. 安裝

```bash
cd webapp-city-reframe-audition
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. 啟動服務

```bash
uvicorn auditor.main:app --port 8080
```

開啟瀏覽器：http://localhost:8080

### 3. 上傳 PDF 審查

1. 點選上傳區域或拖曳 PDF 檔案（可同時上傳事業計畫書 + 權利變換計畫書）
2. 點「開始審查」
3. 等待約 30 秒（大型 PDF 需較長時間）
4. 查看審查報告，報告可列印或儲存為 PDF

## 審查規則（10 條，111年版）

| 規則 ID | 說明 | 法源 |
|---------|------|------|
| DOC-001 | 申請書存在 | 111年版注意事項第1條 |
| DOC-002 | 切結書存在 | 111年版注意事項第1條 |
| DOC-003 | 委託書至少1份 | — |
| DOC-004 | 審議資料表存在 | 111年版修訂第2點 |
| FORM-001 | 送審類別已勾選 | — |
| FORM-002 | 審議資料表填表日期 | — |
| CALC-001 | 容積獎勵不超過上限 | 都更條例第65條 |
| CALC-002 | 無障礙停車位法定數量 | 建築技術規則第167條之六 |
| FORM-003 | 充電車位欄位（111年新增）| 111年版修訂第2點 |
| PII-001 | 高風險個資遮蔽確認 | 個資法第5條 |

## 已測試案例

| 案件 | PDF | 偵測到的問題 |
|------|-----|------------|
| 士林芝山段（案一） | `1130902 1-事業計畫報告書.pdf` | 🔴 CALC-001：容積獎勵超上限 50.95m² |
| 合家歡東湖段（案二） | `1131114...權利變換計畫.pdf` | 前置頁為掃描圖檔，限制詳見下方說明 |

## 已知限制

1. **掃描式 PDF**：如前置頁（申請書/切結書/委託書/審議資料表）為掃描圖片，無法自動解析，需人工核對
2. **超大 PDF（>100MB）**：建議拆冊或壓縮後上傳
3. **本系統為初步篩查工具**：規則引擎標記的問題仍需人工確認，最終審查結論以人工判斷為準

## 加入新規則

1. 在 `auditor/rules/document.py`、`form.py` 或 `pii.py` 繼承 `Rule` 並實作 `evaluate()`
2. 在 `auditor/rules/engine.py` 的 `build_default_engine()` 加入新規則
3. 在 `tests/test_rules.py` 新增對應測試（TDD 原則：先寫測試）

```bash
# 執行單元測試
pytest tests/test_rules.py -v
```

## 技術架構

```
auditor/
├── parsers/          # PDF 文字/表格提取（pdfplumber + NFKC normalization）
├── extractors/       # 審議資料表萃取、封面文件識別、個資偵測
├── rules/            # 規則引擎（Rule 抽象類別 + 10 條規則實作）
├── reporters/        # HTML 報告生成（Jinja2）
└── main.py           # FastAPI 服務入口
templates/            # 上傳頁面 + 報告模板
tests/                # 單元測試
```

**主要依賴**：Python 3.9+、FastAPI、pdfplumber、Jinja2
