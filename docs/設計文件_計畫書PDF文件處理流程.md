# 設計文件｜都更計畫書 PDF 文件處理流程

> **用途**：團隊技術設計規格 ＋ 補助計畫書「貳-三 實施方法·A/B 軌」詳細設計素材。
> **回答的問題**：使用者提出的四步結構（①依範本切章節 ②定位必要頁 ③判頁型 ④掃描表格啟 OCR）我們的流程是否包含？→ **本文逐步標示現況（✅ 有／🟡 部分／❌ 缺）並補上完整設計。**
> 相關：`技術備忘_PDF表格分層讀取策略.md`、`reference_pdf_table_extraction.md`、CLAUDE.md 架構章節。

---

## 一、設計目標與原則

**目標**：把一份數百頁、**混合型**（文字頁＋掃描表格頁）的都更計畫書 PDF，轉為「可稽核、零幻覺」的結構化審查資料。

**四大原則**：
1. **範本驅動（top-down）**：先用官方範本母版理解「這份文件該長什麼樣」，再由結構往下定位、擷取——而非盲目逐頁掃。
2. **逐頁路由**：每頁依型態（文字／掃描）走最適引擎，不一招打天下。
3. **確定性優先、AI 輔助**：能用範本／規則確定的（結構、門檻、版本）不交 AI；AI 只做讀圖與語意初判。
4. **主權地端、資料不出境**：全程地端；真案個資去識別化後才進語意層。

---

## 二、端到端流程總覽（六階段）

```
【階段 0】文件受理 + 逐頁分類 (triage)
        │  每頁 → (文字型 / 掃描型) + 頁碼
        ▼
【階段 1】依官方範本母版切割子章節 ★使用者步驟①（目前 🟡 部分，待建頁範圍切割）
        │  壹~拾捌 / 附錄一~廿四 → 各對應 PDF 頁範圍
        ▼
【階段 2】定位必要文件頁 ★使用者步驟②
        │  申請書 / 切結書 / 委託書(授權書) / 審議資料表 / 必附附錄
        ▼
【階段 3】該頁文字 or 掃描？★使用者步驟③
        │  文字頁→抽文字層(快準)  掃描頁→ 下一階段
        ▼
【階段 4】審議資料表擷取（掃描→表格＋OCR/VLM 分層）★使用者步驟④
        │  VLM 讀圖直出欄位 JSON（主）→ PP-Structure→幾何→Claude vision（退）
        ▼
【階段 5】確定性驗算 + 反幻覺 + 人工覆核
           格式母版校正 / 規則恆等式核算 / 報核日期跨子表 / 標待覆核
```

---

## 三、逐階段詳述（目的 / 認知方法 / 現況 / 完整設計）

### 階段 0　文件受理與逐頁分類（triage）

- **目的**：先知道每頁是文字型還是掃描型，作為後續路由依據。
- **認知方法**：以 PyMuPDF 算每頁「可抽取字元數」，低於門檻（`_MIN_TEXT_CHARS = 30`）判為掃描頁。
- **現況**：✅ **有**——`parsing_pipeline/triage.py::triage_pdf()` 回傳每頁 `PageClass(is_scanned, char_count)`；`parsers/page_text.py::read_page()` 逐頁路由（文字層 / VLM / OCR / empty）。
- **完整設計**：triage 結果應成為**全流程共用的頁面型態索引**，供階段 2–4 查詢（避免各模組重複判定）。

### 階段 1　依官方範本母版切割子章節　★步驟① 🟡 部分（核心待建）

- **目的**：用官方 113 年版範本結構（事業計畫書 18 章 24 附錄…），把整份 PDF **切成「章節 → 頁範圍」對照表**，後續定位與校正都在正確章節內進行。
- **認知方法（完整設計）**：
  1. 取**機讀格式母版**的章節序標與標題（壹、貳…附錄一…；已由 `template_schema.py` 從官方 ODT 產出）。
  2. **偵測目錄頁（TOC）**：解析「壹………3」式目錄列 → 得各章起始頁（`front_docs.py` 已有 `_TOC_MARKERS` 可重用）。
  3. **標題行比對回填**：對每頁抽出的文字/標題，用母版章名核心關鍵字比對，校正 TOC 推得的頁範圍（處理 TOC 頁碼與實際印刷頁差）。
  4. 產出 `{章節marker → (起頁, 迄頁), 型態}` 之**章節分段索引**。
- **現況**：🟡 **部分**——我們**已有母版與結構「存在性」檢查**（`extractors/format_checker.py`：缺章節 FMT-CH／缺附錄 FMT-AP／擇一 FMT-XOR），但**尚無「章節→頁範圍」的切割**（grep 確認無此邏輯）。目前定位靠階段 2 的關鍵字掃描整份前段，而非章節內定位。
- **待建（本設計新增）**：`extractors/chapter_segmenter.py`（暫名）——輸入 PDF＋母版＋triage，輸出章節分段索引。**效益**：①階段 2 可**在正確章節內找必要頁**（更準、更快）；②格式校正從「有沒有」升級為「在對的位置、且完整」；③跨頁表格可鎖定章節邊界，減少錯位。

### 階段 2　定位必要文件頁（委託書/授權書/審議資料表/附錄）　★步驟② ✅ 有

- **目的**：找出審查必看的關鍵文件所在頁——申請書、切結書、**委託書（＝授權書）**、審議資料表，以及必附附錄。
- **認知方法**：
  - **關鍵字比對**（`front_docs.py::_DOC_KEYWORDS`）：申請書／切結書／委託書／審議資料表各有詞庫。
  - **目錄防誤判**（`from_toc`）：若命中處是目錄頁（含 `_TOC_MARKERS`），標記 `from_toc=True`、頁碼記為「目錄列出、實際頁待確認」，避免把目錄當文件頁。
  - **內容標記確認**（`_POA_CONTENT_MARKERS`／`_APPLICATION_CONTENT_MARKERS`）：真正的申請書/委託書頁有申請人、文號等目錄沒有的細節，用以確認。
  - **委託書用途**（`_match_poa_purpose`）：都更規劃／建築設計等。
  - 附錄偵測：`extractors/attachments.py::detect_attachments()` 比對 24 項附錄關鍵字。
- **現況**：✅ **有**——`extractors/front_docs.py::extract_front_docs()`（掃前 60 頁）＋ `review_table.py::_find_review_table_page()`（找審議資料表頁）＋ `attachments.py`。
- **與步驟②的差異＆優化**：使用者說「在**首個子章節**中找」；實際上申請書/切結書/委託書/審議資料表多在**前置文件區**（壹章之前），部分證明文件在**特定附錄**。目前做法是「掃前 60 頁」平掃；**接上階段 1 後**，可把搜尋**限縮到母版定義的正確章節/附錄範圍**，更準確、避免誤命中正文中提及的字樣。

### 階段 3　頁面型態辨識（文字 / 掃描）　★步驟③ ✅ 有

- **目的**：對「要擷取的目標頁」判定文字或掃描，決定用文字層還是 OCR/VLM。
- **認知方法**：沿用階段 0 的 triage（字元數門檻）。`page_text.py::read_page()`：原生字元 ≥ `_MIN_NATIVE_CHARS` → 走文字層（`text`）；否則 → VLM（`vlm`，主權＋繁中較準）→ 退 PaddleOCR（`ocr`）。
- **現況**：✅ **有**——`triage.py` ＋ `page_text.py`。
- **完整設計**：審議資料表即使「整頁是掃描表格」，也走此判定；判為掃描 → 進階段 4 的表格專用管線（而非只做整頁 OCR 轉文字）。

### 階段 4　審議資料表擷取（掃描→表格＋OCR/VLM 分層）　★步驟④ ✅ 有

- **目的**：把掃描的密集審議資料表（20＋欄、兩層表頭）轉成**結構化欄位 JSON**（案名、地號、面積、容積、獎勵、停車、日期、實施者…），而非只是一堆文字。
- **認知方法（主權感知分層，cheap/local first → escalate）**：
  1. **Tier 0：地端 VLM 讀圖直出欄位**（`table_extractor.py` 呼叫 `parsers/vlm_reader.py`）——`VLM_ENDPOINT` 設定時啟用，讀整張表直接對映 `ReviewTableData` 欄位。**繞開拼欄**，是主力。`image_max_pixels`＝8M。
  2. **Tier 1：PaddleOCR PP-Structure**（`_extract_via_ppstructure`）——表格結構辨識，輸出 cells → `_map_structured_cells` 映欄位。
  3. **Tier 2：幾何 bbox 重建**——用座標拼欄（傳統法，易錯，僅補漏）。
  4. **Tier 3：Claude vision 升級**——critical 欄位覆蓋率 < 0.6 時，送雲端視覺辨識（非 PII / 已授權情境）。
  - **只補文字層抓不到的欄位、絕不覆寫**（`_merge_into`）；critical 欄位覆蓋率 `_coverage` 作為升級門檻。
- **現況**：✅ **有**——`extractors/table_extractor.py`（分層）＋ `review_table.py`（找頁＋欄位擷取）＋ `parsers/vlm_reader.py`（VLM client）＋ `extractors/template_anchor.py`（已知欄位區域裁切補值，如基準容積）。
- **完整設計補充**：報核日期屬**跨子表推理**——須自「辦理過程」子表的「報核」列取最新一筆（`review_table.py::_extract_filing_date_from_process`），此為版本選擇（107/108/111/113）的關鍵輸入。

### 階段 5　確定性驗算 + 反幻覺 + 人工覆核（下游，審查級關鍵）

- **格式母版校正**（`format_checker.py`）：缺章節/附錄/擇一，100% 可解釋。
- **規則引擎恆等式驗算**（`rules/calc.py`、`consistency.py`）：容積獎勵 ≤ 基準×50%（§65）、實設停車位＝平面＋機械＋無障礙＋充電、面積/日期跨欄一致。
- **反幻覺**：任何 LLM 步驟套「標明來源、找不到就說找不到、絕不推算」；`confidence_scorer.py` 低信心 → 強制人審。
- **人在迴路**：協審報表標「待覆核」，掃描數字逐格對照原檔。

---

## 四、認知（辨識）方法總表

| 要辨識什麼 | 方法 | 模組 | 狀態 |
|---|---|---|---|
| 頁面文字/掃描 | PyMuPDF 字元數門檻(<30) | `triage.py`、`page_text.py` | ✅ |
| 該文件該有哪些章節/附錄 | 官方 ODT → 機讀母版 | `template_schema.py` | ✅ |
| 章節 → 頁範圍切割 | TOC 解析＋標題比對回填 | **待建** `chapter_segmenter.py` | ❌ |
| 申請書/切結書/委託書頁 | 關鍵字＋TOC 防誤判＋內容標記 | `front_docs.py` | ✅ |
| 審議資料表頁 | 關鍵字定位 | `review_table.py` | ✅ |
| 附錄是否檢附 | 附錄名關鍵字 | `attachments.py` | ✅ |
| 審議表欄位值(掃描) | VLM 直出→PP-Structure→幾何→Claude vision | `table_extractor.py`、`vlm_reader.py` | ✅ |
| 已知欄位補值 | 版面座標裁切 OCR | `template_anchor.py` | ✅ |
| 報核日期 | 辦理過程子表最新報核列 | `review_table.py` | ✅ |
| 格式合規 | 母版比對 | `format_checker.py` | ✅ |
| 數值合規 | 規則恆等式核算 | `rules/calc.py`、`consistency.py` | ✅ |

---

## 五、現況 vs 使用者提案的四步（對照結論）

| 使用者步驟 | 我們是否包含 | 說明 |
|---|---|---|
| ① 依範本規範切割子章節 | 🟡 **部分** | 已有母版與結構「存在性」檢查，**但缺「章節→頁範圍」切割**（待建 `chapter_segmenter.py`）|
| ② 找出委託書/授權書/審議資料表等必要頁 | ✅ **有** | `front_docs`＋`review_table`＋`attachments`；目前平掃前 60 頁，接上①後可章節內定位 |
| ③ 辨識頁面文字/掃描型態 | ✅ **有** | `triage`＋`page_text` 逐頁路由 |
| ④ 審議資料表掃描→表格＋OCR | ✅ **有** | `table_extractor` 四層（VLM 主、OCR/幾何/vision 退）|

> **一句話**：②③④ 都已實作且運作；**①「按範本切章節頁範圍」是唯一明顯缺口**，補上後能讓②的定位更準、格式校正更完整、跨頁表格更穩——建議列為近期強化項（可對應計畫書 A/B 分項工作項目）。

---

## 六、對應計畫書分項（供 B 軌技術說明引用）

- **A 分項**：階段 4（審議表 VLM 辨識微調）、階段 3（頁型路由）。
- **B 分項**：階段 1（母版切章節，B1 延伸）、階段 2（必要頁定位）、階段 5（格式校正 B1／規則核算 B2／協審報表 B4）。
- **待建 `chapter_segmenter.py`** 建議納入 **B1 格式母版校正引擎** 之延伸工作項目（母版不只查「有沒有」，並切「在哪幾頁」）。

---

## 七、資料流與模組對照（一頁速查）

```
PDF ─► triage.py ─────────────► 逐頁型態索引
      └► [待建] chapter_segmenter ► 章節→頁範圍
                    │
   front_docs.py ───┤► 申請書/切結書/委託書 + PII + 報核日期
   review_table.py ─┤► 審議資料表頁 + 欄位(呼叫 table_extractor)
   attachments.py ──┘► 附錄清單
                    │
   table_extractor.py ► VLM/PP-Structure/幾何/vision ► ReviewTableData
   template_anchor.py ► 已知欄位補值
                    │
   ▼ 匯總 AuditData
   format_checker + rules(engine/form/calc/consistency/pii) ► Findings
   ▼
   html_reporter ► 協審報表(附法源/計算式/待覆核)
```

---

*本設計文件依 2026-08 實際 codebase 核對（triage/page_text/front_docs/review_table/table_extractor/template_schema/format_checker）。標「待建」者為現況缺口與建議強化。*
