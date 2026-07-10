"""審議資料表 高解析重輸出：把原始 PDF 以 ≥250 DPI 轉成 PNG（提升 OCR/VLM 辨識與標註）。

背景：客戶提供的訓練圖多為 ~120 DPI，密表 OCR 對中文標籤辨識差。從原始 PDF 以
300 DPI 重輸出後，OCR 自動填率與 VLM 準度都會明顯提升。

用法：
    # 單檔、指定審議資料表頁（你已知頁碼時最快、免 OCR）
    python reexport_highres.py 大魯閣-事業計畫.pdf --pages 19 --dpi 300

    # 整個資料夾、自動偵測每份的審議資料表頁（需 OCR：rapidocr 或文字層）
    python reexport_highres.py ./原始PDF資料夾 --auto --dpi 300 --out ./高解析PNG

    # 指定多頁 / 整份
    python reexport_highres.py x.pdf --pages 6,7      # 多頁
    python reexport_highres.py x.pdf --all            # 每頁（大檔慎用）

輸出：<out>/<檔名>_p<頁>_<dpi>dpi.png
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
from typing import List, Optional

# 審議資料表頁的判別詞：標題 + 至少一個資料表專有詞（避免抓到目錄頁的標題引用）
_TITLE_MARKERS = ["審議資料表", "都市更新審議資料表"]
_CONTENT_MARKERS = ["送審類別", "基準容積", "辦理過程", "獎勵樓地板", "汽車停車位", "填表日期"]
_AUTO_SCAN_PAGES = 30  # 自動偵測只掃前 N 頁（審議資料表在前置文件）


def _page_text_or_ocr(doc, idx: int, ocr) -> str:
    """回傳該頁文字：有文字層直接用；否則（掃描頁）用 OCR。"""
    t = doc[idx].get_text()
    if t.strip():
        return t
    if ocr is None:
        return ""
    import fitz
    pix = doc[idx].get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
    import io
    from PIL import Image
    im = Image.open(io.BytesIO(pix.tobytes("png")))
    import numpy as np
    res = ocr(np.array(im))
    items = res[0] if isinstance(res, tuple) else res
    return " ".join((it[1] or "") for it in (items or []))


def _make_ocr():
    try:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError:
            from rapidocr import RapidOCR
        return RapidOCR()
    except Exception:
        return None


def detect_review_pages(pdf_path: str) -> List[int]:
    """自動偵測審議資料表頁（1-based）。掃前 30 頁，命中標題 + 內容詞即採計。"""
    import fitz
    ocr = None
    doc = fitz.open(pdf_path)
    # 若前段頁面都無文字層才建立 OCR（省時）
    need_ocr = not any(doc[i].get_text().strip() for i in range(min(len(doc), _AUTO_SCAN_PAGES)))
    if need_ocr:
        ocr = _make_ocr()
        if ocr is None:
            print("  ⚠ 掃描件需 OCR 但未安裝 rapidocr；請改用 --pages 指定頁碼", file=sys.stderr)
    hits = []
    for i in range(min(len(doc), _AUTO_SCAN_PAGES)):
        txt = _page_text_or_ocr(doc, i, ocr)
        if any(m in txt for m in _TITLE_MARKERS) and any(m in txt for m in _CONTENT_MARKERS):
            hits.append(i + 1)
    doc.close()
    return hits


def render_pages(pdf_path: str, pages: List[int], dpi: int, out_dir: str) -> List[str]:
    import fitz
    doc = fitz.open(pdf_path)
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    written = []
    for p in pages:
        if p < 1 or p > len(doc):
            print(f"  ⚠ {stem} 無第 {p} 頁（共 {len(doc)} 頁），略過", file=sys.stderr)
            continue
        pix = doc[p - 1].get_pixmap(matrix=mat, alpha=False)
        out = os.path.join(out_dir, f"{stem}_p{p}_{dpi}dpi.png")
        pix.save(out)
        written.append(out)
        print(f"  ✓ {stem} p{p} → {pix.width}x{pix.height}px ({os.path.getsize(out)//1024} KB)")
    doc.close()
    return written


def main():
    ap = argparse.ArgumentParser(description="審議資料表高解析重輸出（PDF → ≥250 DPI PNG）")
    ap.add_argument("input", help="PDF 檔或含 PDF 的資料夾")
    ap.add_argument("--dpi", type=int, default=300, help="輸出 DPI（預設 300；建議 ≥250）")
    ap.add_argument("--pages", default=None, help="要輸出的頁碼（1-based，逗號分隔，如 19 或 6,7）")
    ap.add_argument("--auto", action="store_true", help="自動偵測審議資料表頁（掃描件需 rapidocr）")
    ap.add_argument("--all", action="store_true", help="輸出每一頁（大檔慎用）")
    ap.add_argument("--out", default="高解析PNG", help="輸出資料夾（預設 ./高解析PNG）")
    args = ap.parse_args()

    if args.dpi < 200:
        print(f"⚠ DPI {args.dpi} 偏低，密表建議 ≥250（甚至 300）", file=sys.stderr)

    pdfs = ([args.input] if args.input.lower().endswith(".pdf")
            else sorted(glob.glob(os.path.join(args.input, "**", "*.pdf"), recursive=True)))
    if not pdfs:
        print("找不到 PDF", file=sys.stderr)
        return 2
    os.makedirs(args.out, exist_ok=True)

    explicit_pages: Optional[List[int]] = None
    if args.pages:
        explicit_pages = [int(x) for x in args.pages.replace("，", ",").split(",") if x.strip()]

    import fitz
    total = 0
    for pdf in pdfs:
        name = os.path.basename(pdf)
        if args.all:
            pages = list(range(1, fitz.open(pdf).page_count + 1))
        elif explicit_pages is not None:
            pages = explicit_pages
        elif args.auto:
            pages = detect_review_pages(pdf)
            print(f"[{name}] 自動偵測審議資料表頁：{pages or '（未找到，請用 --pages 指定）'}")
        else:
            print(f"[{name}] 請指定 --pages（頁碼）、--auto（自動偵測）或 --all", file=sys.stderr)
            continue
        if pages:
            total += len(render_pages(pdf, pages, args.dpi, args.out))

    print(f"\n✓ 共輸出 {total} 張 {args.dpi} DPI PNG → {args.out}/")
    print("  下一步：重跑 make_annotations.py（換這批高解析圖）→ 自動填率會提升。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
