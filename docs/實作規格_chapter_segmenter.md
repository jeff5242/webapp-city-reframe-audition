# 實作規格｜`chapter_segmenter`（依範本切割章節頁範圍）

> **給工程用**：介面、演算法、邊界情況、測試計畫、驗收標準。
> **目的**：把整份計畫書 PDF 依官方範本母版切成「章節 marker → PDF 頁範圍」，補上流程階段①的缺口。
> **前置**：沿用 `extractors/template_schema.py`（母版）、`front_docs.py`（TOC 標記）、`parsers/page_text.py`（逐頁文字）、`parsing_pipeline/triage.py`（頁型）。
> **慣例**：`from __future__ import annotations`、frozen dataclass、type hints、純函式與 IO 分離、**抓不到回 None/略過、絕不誤報**。

---

## 一、目標與範圍

**輸入**：PDF 路徑 ＋ `TemplateSchema`（由 `load_schema(doc_type, version)` 取得）。
**輸出**：每個母版章節（壹~拾捌／附錄一~廿四）對應的 `(起頁, 迄頁)`（1-based, inclusive）、來源、信心。

**v1 範圍**：單一文件類型（例：事業計畫書）之單份 PDF。
**v1 不做**（列 backlog）：一份 PDF 內含事業計畫書＋權利變換兩文件之切分；跨頁表格內部再切；掃描頁無文字層時的標題偵測（v1 依賴文字層／頁面文字供給器，掃描頁標題交給 VLM 供文字後再比對）。

---

## 二、介面

```python
# auditor/extractors/chapter_segmenter.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple
from .template_schema import TemplateSchema, TemplateSection

# 逐頁文字供給器：page_num(1-based) -> text。預設用 page_text.pages_text 包一層；
# 測試時注入假資料（與 template_schema 測試同風格，免真 PDF）。
PagesTextFn = Callable[[str], Dict[int, str]]

Source = str  # "toc+heading" | "heading" | "toc" | "inferred"


@dataclass(frozen=True)
class ChapterSpan:
    marker: str            # 壹 / 貳 / … / 附錄一
    title: str             # 章名（去序標）
    kind: str              # "chapter" | "appendix"
    order: int             # 母版順序（沿用 TemplateSection.order）
    start_page: int        # 1-based, inclusive
    end_page: int          # 1-based, inclusive（末章 = 文件末頁）
    source: Source         # 頁碼來源
    confidence: float      # 0..1


@dataclass(frozen=True)
class SegmentationResult:
    doc_type: str
    version: str
    spans: Tuple[ChapterSpan, ...]        # 依 order 排序、頁碼單調遞增
    unresolved: Tuple[str, ...]           # 母版有、但定位不到的 marker（多為 choose_one 未填/選附）
    toc_pages: Tuple[int, ...]            # 偵測到的目錄頁
    page_offset: Optional[int]            # TOC 印刷頁 → PDF 頁 之校正量（None=無 TOC 或無法校正）


# --- 主入口 ---
def segment_chapters(
    pdf_path: str,
    schema: TemplateSchema,
    *,
    pages_text: Optional[PagesTextFn] = None,   # 注入用；預設讀真 PDF
    total_pages: Optional[int] = None,          # 末章迄頁用；預設由 PDF 取得
) -> SegmentationResult: ...


# --- 純函式（各自可單測，不碰 IO）---
def _parse_toc(page_texts: Dict[int, str]) -> Tuple[Dict[str, int], Tuple[int, ...]]:
    """回傳 ({marker: 印刷頁碼}, 目錄頁清單)。無 TOC → ({}, ())。"""

def _detect_headings(page_texts: Dict[int, str], schema: TemplateSchema) -> Dict[str, int]:
    """回傳 {marker: 首次出現該『標題行』的 PDF 頁}。只認行首序標題、排除正文提及。"""

def _calibrate_offset(toc: Dict[str, int], headings: Dict[str, int]) -> Optional[int]:
    """以同時有 TOC 與 heading 的 marker，取 (heading_pdf - toc_printed) 之中位數當 offset。"""

def _resolve_spans(
    schema: TemplateSchema, toc: Dict[str, int], headings: Dict[str, int],
    offset: Optional[int], total_pages: int,
) -> Tuple[List[ChapterSpan], List[str]]:
    """整合 → (spans, unresolved)。end = 下一已定位章 start-1；末章 = total_pages。"""
```

---

## 三、演算法

### 階段流程
```
1. 取逐頁文字 page_texts = pages_text(pdf_path)         (IO；可注入)
2. toc, toc_pages   = _parse_toc(page_texts)            (純)
3. headings         = _detect_headings(page_texts, schema)  (純)
4. offset           = _calibrate_offset(toc, headings)  (純)
5. spans, unresolved= _resolve_spans(schema, toc, headings, offset, total_pages)  (純)
6. return SegmentationResult(...)
```

### 3.1 `_parse_toc`
- **偵測目錄頁**：頁內含任一 `front_docs._TOC_MARKERS`（「目 錄」「目次」「............」…）且**點引導線行 ≥ 3**。
- **解析 TOC 列**：每列 regex 抓「序標＋標題 … 頁碼」：
  ```
  _TOC_LINE = re.compile(
    r'^\s*(附錄[一二三四五六七八九十百]+|[壹貳參肆伍陸柒捌玖拾]+)、'  # 序標
    r'.*?[.．·…\s]{2,}\s*'                                          # 點引導線
    r'(\d+)\s*$'                                                    # 印刷頁碼
  )
  ```
- marker 正規化沿用 `template_schema._MARKER_RE`。回 `{marker: int(頁碼)}`。
- **容錯**：一個 marker 多次命中取第一次；頁碼非數字/羅馬數字 → 跳過（不誤填）。

### 3.2 `_detect_headings`
- 對每頁文字**逐行**掃描，行首（strip 後）符合 `template_schema._MARKER_RE`（`^(附錄…|[壹貳…]+)、`）**且**該行為「標題狀」（長度 < 40、非句子），視為章節標題。
- **排除正文提及**（關鍵防誤判）：
  - 必須**行首**（不是句中出現「…如附錄十九所示…」）。
  - 該 marker 若在 schema 內、且此頁號**大於已接受之前一 marker 頁號**（維持單調）才接受；否則視為正文引用、略過。
  - 同 marker 多頁命中 → 取**最小**頁（首次出現＝章首）。
- 回 `{marker: pdf_page}`（只含 schema 有的 marker）。

### 3.3 `_calibrate_offset`
- 交集 markers（TOC∩heading）計算 `diff = heading_pdf - toc_printed`，取**中位數**為 `offset`（吸收前置頁/羅馬頁碼差、單一離群值）。
- 交集為空 → `offset = None`（無法校正）。

### 3.4 `_resolve_spans`
- **決定每章 start_page（優先序）**：
  1. `heading` 有 → 用 heading 頁（`source=heading`，若 TOC 也有且校正後相符 → `toc+heading`, conf 1.0）。
  2. 僅 `toc` 有且 `offset` 可用 → `toc_printed + offset`（`source=toc`, conf 0.6）。
  3. 皆無 → 不建 span，列入 `unresolved`（多為 choose_one 未填 / 選附未附，屬正常）。
- **單調性保證**：依 schema `order` 排序後，start_page 必須遞增；違反者（前章頁 > 後章頁）標記該章為 `inferred`（conf 0.4）並用「前章 start ~ 後一個已定位章 start」內插修正，或降信心保留待人工。
- **end_page**：= 下一個「已定位」章的 start_page − 1；最後一章 = `total_pages`。
- 附錄同理（附錄一~廿四接在拾捌之後）。

### 信心分數
| 情境 | source | confidence |
|---|---|---|
| TOC 與 heading（校正後）相符 | toc+heading | 1.0 |
| 僅 heading | heading | 0.9 |
| 僅 TOC（offset 校正） | toc | 0.6 |
| 單調修正/內插 | inferred | 0.4 |

---

## 四、邊界情況與決策

| 情況 | 處理 |
|---|---|
| 無目錄頁 | `toc={}`、`offset=None` → 全靠 heading；heading 也無的 marker 進 unresolved |
| TOC 頁碼＝印刷頁（與 PDF index 差前置頁）| offset 校正吸收 |
| 正文提及「附錄十九」但非章首 | `_detect_headings` 行首＋單調＋標題狀 三重過濾排除 |
| choose_one（陸/柒/捌 擇一）| 未出現者進 unresolved（正常，非錯誤）；出現者正常建 span |
| 掃描頁無文字層（章首在掃描頁）| v1：`pages_text` 供給器對掃描頁回 VLM 轉錄文字（`page_text` 已支援）；仍抓不到 → unresolved（不誤切）|
| 一份 PDF 兩文件（事業＋權變）| v1 不處理，`doc_type` 需呼叫端指定；backlog 做前置分割 |
| 完全對不上（版面非預期）| spans 可能大量 unresolved → 呼叫端據此**略過章節內定位、退回平掃**（不可 crash、不可誤切）|

---

## 五、測試計畫（pytest，合成 fixture、免真 PDF）

檔案：`tests/test_chapter_segmenter.py`。沿用 `test_template_schema.py` 的注入手法：`pages_text` 傳 `lambda _: FAKE_PAGES`。

```python
# 合成一份「事業計畫書」頁面文字：P1 封面、P2 目錄、P3 壹、P5 貳 … P40 附錄一 …
def fake_pages():
    return {
        1: "都市更新事業計畫書 封面",
        2: "目 錄\n壹、計畫緣起與目標 ............ 1\n貳、計畫地區範圍 ............ 3\n附錄一、實施者證明文件 ...... 38",
        3: "壹、計畫緣起與目標\n本計畫……",
        4: "……內文……",
        5: "貳、計畫地區範圍\n……",
        # …
        40: "附錄一、實施者證明文件\n……",
    }
```

### 必測案例
| # | 測試 | 驗證點 |
|---|---|---|
| 1 | `_parse_toc` 解析點引導線 | 「壹、… 1」→ `{"壹":1,...}`；回傳 toc_pages=[2] |
| 2 | `_parse_toc` 無目錄 | 回 `({}, ())`，不 crash |
| 3 | `_detect_headings` 行首序標題 | P3「壹、」→ `{"壹":3}` |
| 4 | `_detect_headings` **排除正文提及** | 內文「…見附錄十九…」不得產生 heading |
| 5 | `_detect_headings` 同 marker 多頁取最小 | 章跨頁時 start = 首頁 |
| 6 | `_calibrate_offset` 中位數 | TOC 印刷頁 1/3 vs heading PDF 3/5 → offset=2 |
| 7 | `_calibrate_offset` 離群容忍 | 一筆離群不影響中位數 |
| 8 | `_resolve_spans` end=next start-1 | 壹(3~4)、貳(5~…) |
| 9 | `_resolve_spans` 末章 = total_pages | 最後附錄 end = 文件末頁 |
| 10 | heading 優先於 toc | 兩者不一致時取 heading，source=heading/toc+heading |
| 11 | 僅 TOC → offset 推算 | source=toc, confidence=0.6 |
| 12 | choose_one 未出現 | 進 unresolved、不建 span、不算錯 |
| 13 | 單調性違反 → inferred | 亂序不 crash，降信心保留 |
| 14 | **整合** `segment_chapters` 注入 fake_pages | spans 依 order、start_page 單調遞增；unresolved 合理 |
| 15 | 真 schema 載入 | `load_schema("事業計畫書","113")` + 合成頁 → chapters≤18、appendices≤24、頁碼遞增 |
| 16 | 版面全不符 | 大量 unresolved、回傳有效物件（呼叫端可退回平掃），**不 crash** |

**覆蓋率目標**：純函式（_parse_toc/_detect_headings/_calibrate_offset/_resolve_spans）≥ 90%；全套 `pytest tests/ -q` 綠。

---

## 六、整合點（實作後接線）

1. **`main.py` /audit 流程**：`schema = load_schema(doc_type, reg_year)`（doc_type 由文件判定）→ `seg = segment_chapters(pdf, schema)`。
2. **`front_docs` / `attachments` 定位限縮**：把搜尋範圍由「前 60 頁平掃」改為「在 `seg` 對應章節/附錄頁範圍內找」——新增可選參數 `page_ranges`，無 seg 時退回現行平掃（向後相容）。
3. **`format_checker` 升級**：由「有沒有」→「在對的頁、且完整」。可將 `ChapterSpan` 併入 Finding 的 evidence（「附錄十九 應在第 X 頁」）。
4. **報表**：協審報表可顯示章節頁範圍，供承辦跳頁核對。

> **向後相容原則**：`segment_chapters` 失敗或低信心 → 呼叫端**退回現行平掃**，不得使既有流程變差。

---

## 七、里程碑與驗收

| 里程碑 | 交付 | 驗收 |
|---|---|---|
| M1 純函式 | `_parse_toc/_detect_headings/_calibrate_offset/_resolve_spans` + 測試 1–13 | 單測綠、覆蓋率 ≥90% |
| M2 主入口 | `segment_chapters` + 測試 14–16 | 合成整合綠、不 crash |
| M3 真檔驗證 | 對去識別化樣本跑，人工比對章首頁 | chapters 定位準確率 ≥ 【90%】 |
| M4 接線 | front_docs/attachments 限縮 + format_checker evidence | 既有 470+ 測試不退；平掃退路可用 |

> 對應計畫書：建議列為 **B1 格式母版校正引擎**之延伸工作項目（母版不只查「有沒有」，並切「在哪幾頁」）。

---

*本規格依 2026-08 codebase（template_schema/front_docs/page_text/triage）設計。實作前確認 `load_schema` 與 `pages_text` 介面未變。*
