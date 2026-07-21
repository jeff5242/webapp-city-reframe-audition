# 格式母版 schema（官方範本 → 機讀結構）

本目錄存放由臺北市都更處**官方公開範本 ODT** 解析出的機讀格式 schema
（`schema_<version>_<doc>.json`）。這些 schema 是格式校正（`format_checker`）
與「附錄必附規則自動產生」的 ground truth——**免人工標註**。

## 檔案

| 檔案 | 文件類型 | 章 / 附錄 |
|---|---|---|
| `schema_113_事業計畫書.json` | 事業計畫書 | 18 / 24 |
| `schema_113_權利變換計畫書.json` | 權利變換計畫書 | 12 / 7 |
| `schema_113_事業概要計畫書.json` | 事業概要計畫書 | 12 / 9 |

每節記錄 `marker`（壹/貳/…/附錄一…）、`title`、`kind`（chapter/appendix）、
`requirement`（required / choose_one / optional）、`order`。必附性由官方標題內的
「（請擇一填寫）／（若無則免附）／（請視實際情形檢附）」自動判定。

## 來源（113 年 12 月 3 日修正公布版）

公告頁：<https://uro.gov.taipei/News_Content.aspx?n=9CAC69257CFAC70E&sms=1635F69C7777535B&s=D10AC6C64702DCAA>

範本 ODT（官方空白範本，**不含個資**）：
- 事業計畫書：`.../8540413/126ee8e3-a485-49da-9407-a734aa97c712.odt`
- 權利變換計畫書：`.../8540413/72d3e1eb-cb51-4dcd-aa82-8d266e3ce013.odt`
- 事業概要計畫書：`.../8540413/fd01d4fb-cb7e-457b-82a9-0fb16a66d5d6.odt`

（主機字首 `https://www-ws.gov.taipei/001/Upload/459/relfile/22615/`）

> 附件冊範本非章節式文件（一疊表單），不適用本 schema，故不產生。

## 重新產生

範本改版時，下載新 ODT 到一個目錄後執行：

```bash
python scripts/build_template_schema.py <ODT目錄> --version <年版>
```

產生的 JSON 為**草稿**，須經承辦／委員複核後才視為上線母版
（沿用本專案「AI 產出一律草稿、人工簽核才上線」原則）。
