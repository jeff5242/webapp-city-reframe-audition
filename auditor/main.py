from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

# 臺北時區（UTC+8，全年無日光節約）——容器預設 UTC，審查時間須以台北時間呈現。
_TAIPEI_TZ = timezone(timedelta(hours=8))
from pathlib import Path
from typing import List, Optional

log = logging.getLogger(__name__)

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .annotator import annotate_pdf
from .pii_masker import mask_pii, masking_enabled
from .extractors.front_docs import extract_front_docs
from .s3 import (
    delete_case_meta,
    download_to_temp,
    generate_download_url,
    generate_upload_url,
    list_cases,
    load_labels,
    s3_available,
    save_annotated_pdf,
    save_case_meta,
    save_labels,
    setup_bucket_encryption,
)
from .wiki import load_all_wiki
from .extractors.review_table import extract_review_table
from .extractors.term_checker import extract_number_contexts, scan_for_wrong_terms
from .models import AiFinding, AuditData, AuditReport, FindingDiff
from .reporters.evidence_snapshot import render_evidence_thumbnails
from .reporters.html_reporter import _evidence_page, generate_report
from .reporters.method_docs import METHOD_DOCS
from .rules.engine import build_default_engine, build_engine_with_playbook
from .storage.history import delete_test_runs, get_prev_run, get_peer_stats, init_db, save_run, save_run_metrics
from .version_selector import select_version

_DOCS_DIR = Path(__file__).parent.parent / "docs"

def _read_app_version() -> str:
    """Read git commit hash from VERSION file (written at deploy time)."""
    try:
        ver_file = Path(__file__).parent.parent / "VERSION"
        if ver_file.exists():
            return ver_file.read_text().strip()[:7]
    except Exception:
        pass
    try:
        import subprocess
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=2,
            cwd=str(Path(__file__).parent.parent),
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"

APP_VERSION = _read_app_version()


def _date_from_filename(filename: str) -> str | None:
    """Extract ROC date from filenames like '1131114【案名】' (YYYMMDD = 113年11月14日)."""
    m = re.search(r'(?:^|[_\-\s])(\d{3})(\d{2})(\d{2})(?:[^0-9]|$)', filename)
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if 100 <= y <= 130 and 1 <= mo <= 12 and 1 <= d <= 31:
        return f"{y}年{mo}月{d}日"
    return None


def _load_wiki_rules() -> str:
    wiki_path = _DOCS_DIR / "urban-renewal-111-wiki.md"
    try:
        text = wiki_path.read_text(encoding="utf-8")
        return text[:3000]
    except OSError:
        return "臺北市都市更新111年版法規重點（都更條例、容積獎勵辦法）"


def _pdf_to_markdown(pdf_path: str) -> str:
    """Convert a PDF to Markdown via triage → Docling (text) + Surya (scanned).

    Returns the combined Markdown, or "" if nothing could be extracted.
    Each reader is optional; a missing dependency logs and is skipped.
    """
    from .parsing_pipeline.triage import triage_pdf, text_page_indices, scanned_page_indices

    page_classes = triage_pdf(pdf_path)
    text_pages = text_page_indices(page_classes)
    scanned_pages = scanned_page_indices(page_classes)

    parts: List[str] = []

    if text_pages:
        try:
            from .parsing_pipeline.docling_reader import parse_pages_to_markdown
            md = parse_pages_to_markdown(pdf_path, text_pages)
            if md.strip():
                parts.append(md)
        except Exception as exc:
            log.debug("Docling unavailable or failed: %s", exc)

    if scanned_pages:
        try:
            from .parsing_pipeline.surya_reader import ocr_pages
            ocr_result = ocr_pages(pdf_path, scanned_pages)
            if ocr_result:
                parts.append("\n".join(ocr_result.values()))
        except Exception as exc:
            log.debug("Surya unavailable or failed: %s", exc)

    return "\n\n".join(parts)


def _reg_year_from_version(reg_version) -> str:
    """Extract the year digits (e.g. '111') from a RegulationVersion label like '111年版'."""
    try:
        return reg_version.label.replace("年版", "").strip()
    except Exception:
        return "111"


def _run_ai_pipeline(
    pdf_path: str,
    reg_year: str = "111",
    secondary_pdf: Optional[str] = None,
) -> List[AiFinding]:
    """Run Track B AI pipeline; returns [] when key absent or any step fails.

    Args:
        pdf_path:      primary document (事業計畫書) PDF path
        reg_year:      regulation year ("107"/"108"/"111"/"113") for field validation
        secondary_pdf: optional 權利變換計畫書 PDF for cross-document comparison
    """
    if not os.getenv("ANTHROPIC_API_KEY"):
        return []

    try:
        from .parsing_pipeline.chunker import chunk_markdown
        from .parsing_pipeline.llm_auditor import audit_chunks
        from .parsing_pipeline.field_auditor import extract_and_validate
    except ImportError as exc:
        log.warning("Track B pipeline unavailable: %s", exc)
        return []

    wiki_rules = _load_wiki_rules()
    ai_findings: List[AiFinding] = []

    try:
        combined_md = _pdf_to_markdown(pdf_path)
        if not combined_md.strip():
            return []

        chunks = chunk_markdown(combined_md)

        try:
            from .parsing_pipeline.evidence_grounder import ground_quote
        except ImportError:
            ground_quote = None

        try:
            for f in audit_chunks(chunks, wiki_rules):
                evidence_text: Optional[str] = None
                evidence_verified = False
                if ground_quote is not None:
                    # Critical findings escalate to the Citations API for an
                    # authoritative quote; warnings use the cheap offline check.
                    ev = ground_quote(
                        f.detected_text,
                        combined_md,
                        claim=f.reason,
                        use_citations_api=(f.severity == "critical"),
                    )
                    evidence_text = ev.cited_text
                    evidence_verified = ev.verified
                ai_findings.append(AiFinding(
                    source="llm",
                    rule_id=f.rule_id,
                    severity=f.severity,
                    field_name=f.error_type,
                    detected_text=f.detected_text,
                    reason=f.reason,
                    page_number=f.page_number,
                    evidence_text=evidence_text,
                    evidence_verified=evidence_verified,
                ))
        except Exception as exc:
            log.error("LLM audit error: %s", exc)

        try:
            _, field_findings = extract_and_validate(combined_md, reg_year=reg_year)
            for f in field_findings:
                ai_findings.append(AiFinding(
                    source="field",
                    rule_id=f.rule_id,
                    severity=f.severity,
                    field_name=f.field_name,
                    detected_text=f.actual_value,
                    reason=f.reason,
                    page_number=f.page_number,
                ))
        except Exception as exc:
            log.error("Field audit error: %s", exc)

        # ── Cross-document comparison (事業計畫書 vs 權利變換計畫書) ──
        if secondary_pdf:
            try:
                from .parsing_pipeline.cross_doc_comparator import compare_documents
                secondary_md = _pdf_to_markdown(secondary_pdf)
                if secondary_md.strip():
                    for f in compare_documents(combined_md, secondary_md):
                        ai_findings.append(AiFinding(
                            source="cross",
                            rule_id=f.rule_id,
                            severity=f.severity,
                            field_name=f.field_name,
                            detected_text=(
                                f"事業計畫書：{f.business_plan_value}／"
                                f"權利變換計畫書：{f.rights_exchange_value}"
                            ),
                            reason=f.reason,
                            page_number=0,
                        ))
            except Exception as exc:
                log.error("Cross-doc comparison error: %s", exc)

    except Exception as exc:
        log.error("Track B pipeline error: %s", exc)

    return ai_findings


def _warmup_ocr() -> None:
    """Pre-warm OCR model so the first upload request has no cold-start delay."""
    try:
        from .parsers.ocr_reader import _get_reader
        _get_reader()
    except Exception as exc:
        log.debug("OCR warmup failed (non-fatal): %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    threading.Thread(target=_warmup_ocr, daemon=True, name="ocr-warmup").start()
    yield


app = FastAPI(
    title="臺北市都市更新審議自動審查",
    description="111年版規則 POC",
    version="0.2.0",
    lifespan=lifespan,
)

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_STATIC_DIR = Path(__file__).parent.parent / "static"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
templates.env.globals["APP_VERSION"] = APP_VERSION

if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
# 14 條手寫規則 + playbook 宣告式規則（缺口3 規則外部化）。
# 目前 playbook live 規則為 0（示範規則已標 enabled=false 避免與手寫重複）；
# 待 extractor 支援後，附錄必附等草稿規則 enable 即在此自動生效。
_engine = build_engine_with_playbook("111")

# In-memory cache for annotated PDFs (key → bytes)
_PDF_CACHE: dict[str, bytes] = {}

# Async audit task store: task_id → {status, progress, html, done_at}
_AUDIT_TASKS: dict[str, dict] = {}
_TASKS_LOCK = threading.Lock()


def _set_task_progress(task_id: str, status: str, progress: str) -> None:
    with _TASKS_LOCK:
        if task_id in _AUDIT_TASKS:
            _AUDIT_TASKS[task_id]["status"] = status
            _AUDIT_TASKS[task_id]["progress"] = progress


def _prune_old_tasks() -> None:
    """Remove tasks older than 2 hours to prevent unbounded memory growth."""
    cutoff = time.time() - 7200
    with _TASKS_LOCK:
        stale = [k for k, v in _AUDIT_TASKS.items() if (v.get("done_at") or 0) < cutoff and v.get("done_at")]
        for k in stale:
            del _AUDIT_TASKS[k]


def _run_audit_sync(
    task_id: str,
    bp_s3_key: Optional[str],
    re_s3_key: Optional[str],
    bp_bytes: Optional[bytes],
    bp_filename: Optional[str],
    re_bytes: Optional[bytes],
    re_filename_direct: Optional[str],
) -> None:
    """Full audit pipeline running in a background thread."""
    tmp_dir = tempfile.mkdtemp()
    try:
        _set_task_progress(task_id, "running", "準備 PDF 檔案中…")

        # Resolve primary PDF
        if bp_s3_key:
            _set_task_progress(task_id, "running", "從 S3 下載 PDF 中…")
            primary_pdf = download_to_temp(bp_s3_key, tmp_dir)
            bp_fname = Path(bp_s3_key).name
        else:
            bp_path = Path(tmp_dir) / (bp_filename or "business_plan.pdf")
            bp_path.write_bytes(bp_bytes or b"")
            primary_pdf = str(bp_path)
            bp_fname = bp_filename or "business_plan.pdf"

        # Resolve secondary PDF
        re_path: Optional[Path] = None
        re_fname: Optional[str] = None
        if re_s3_key:
            re_local = download_to_temp(re_s3_key, tmp_dir)
            re_path = Path(re_local)
            re_fname = Path(re_s3_key).name
        elif re_bytes and re_filename_direct:
            re_path = Path(tmp_dir) / re_filename_direct
            re_path.write_bytes(re_bytes)
            re_fname = re_filename_direct

        case_meta_id: Optional[str] = uuid.uuid4().hex[:12] if bp_s3_key else None

        # ── Extract from primary document ──
        _set_task_progress(task_id, "running", "OCR 辨識掃描頁面中（首次可能需 10-20 分鐘）…")
        try:
            review_table = extract_review_table(primary_pdf)
        except Exception:
            review_table = None
        try:
            front_docs, pii_risks = extract_front_docs(primary_pdf)
        except Exception:
            front_docs, pii_risks = None, []
        try:
            term_matches = scan_for_wrong_terms(primary_pdf)
        except Exception:
            term_matches = []
        try:
            number_contexts = extract_number_contexts(primary_pdf)
        except Exception:
            number_contexts = []
        try:
            from .extractors.attachments import detect_attachments_from_pdf
            attachments = detect_attachments_from_pdf(primary_pdf)
        except Exception:
            attachments = None  # 偵測失敗 → 附錄規則 skip，不誤報缺件

        _set_task_progress(task_id, "running", "執行規則檢查中…")
        audit_data = AuditData(
            review_table=review_table,
            front_docs=front_docs,
            pii_risks=tuple(pii_risks),
            term_matches=tuple(term_matches),
            number_contexts=tuple(number_contexts),
            attachments=attachments,
        )
        findings = _engine.evaluate(audit_data)

        # Version selection — 報核日期優先序（理事長指示 2026-07）：
        # ① 審議資料表「辦理過程」報核日 ② 申請書/切結書/委託書報核日
        # ③ 審議資料表填表日期 ④ 檔名日期。第一次申請無審議資料表時，落到 ②。
        review_filing_date = (
            review_table.report_filing_date if review_table else None
        )
        report_date_roc = front_docs.report_date if front_docs and front_docs.report_date else None
        fill_date_fallback = review_table.fill_date if review_table else None
        filename_date_fallback = _date_from_filename(bp_fname)
        version_date = (
            review_filing_date
            or report_date_roc
            or fill_date_fallback
            or filename_date_fallback
        )
        reg_version, fill_date_iso = select_version(version_date)

        # 權利變換計畫書 報核日期分列（可能與事業計畫書不同天）：優先取次文件自己的
        # 審議資料表「辦理過程」報核日（OCR），取不到才退回檔名日期。
        report_date_secondary: Optional[str] = None
        report_date_secondary_source: Optional[str] = None
        if re_path is not None:
            try:
                re_review_table = extract_review_table(str(re_path))
                if re_review_table and re_review_table.report_filing_date:
                    report_date_secondary = re_review_table.report_filing_date
                    report_date_secondary_source = "審議資料表（辦理過程報核日）"
            except Exception:
                log.exception("次文件審議資料表報核日擷取失敗（非致命）")
            if report_date_secondary is None:
                secondary_fname_date = _date_from_filename(re_fname or "")
                if secondary_fname_date:
                    report_date_secondary = secondary_fname_date
                    report_date_secondary_source = "檔名日期（自動辨識）"

        case_name = (
            review_table.case_name if review_table and review_table.case_name else bp_fname
        )
        audit_time = datetime.now(_TAIPEI_TZ).strftime("%Y-%m-%d %H:%M")

        # Diff
        diffs: list[FindingDiff] = []
        prev_audit_time: Optional[str] = None
        prev_run = get_prev_run(case_name)
        if prev_run:
            prev_audit_time = prev_run["audit_time"]
            diffs = _compute_diff(prev_run["findings"], findings)

        # Save run
        save_run(
            case_name=case_name,
            audit_time=audit_time,
            rule_ver=reg_version.label,
            findings_json=json.dumps([
                {"rule_id": f.rule_id, "rule_name": f.rule_name,
                 "status": f.status, "message": f.message, "evidence": f.evidence}
                for f in findings
            ]),
        )

        # Metrics
        _submission_type = review_table.submission_type if review_table else None
        if _submission_type is None and re_s3_key:
            _submission_type = "B-1"
        # 容積獎勵比率 = 獎勵樓地板 ÷ 基準容積（非 ÷ 上限；理事長指示 2026-07）。
        # 基準容積缺漏時，依「上限 = 基準 × 50%」推回 基準 = 上限 × 2。
        _bonus_pct: Optional[float] = None
        if review_table and review_table.bonus_floor_area:
            _base = review_table.base_floor_area
            if not _base and review_table.bonus_limit:
                _base = review_table.bonus_limit * 2
            if _base and _base > 0:
                _bonus_pct = round(review_table.bonus_floor_area / _base * 100, 1)
        _critical_count = sum(1 for f in findings if f.severity == "critical" and f.status == "fail")
        _high_count = sum(1 for f in findings if f.severity == "high" and f.status == "fail")
        _warn_count = sum(1 for f in findings if f.status == "warn")
        _parking_ids = {"CALC-002", "CALC-003"}
        _parking_findings = [f for f in findings if f.rule_id in _parking_ids]
        _parking_pass: Optional[int] = (
            1 if all(f.status == "pass" for f in _parking_findings) else 0
        ) if _parking_findings else None
        _pii_high_count = sum(1 for r in pii_risks if r.severity == "HIGH")
        save_run_metrics(
            case_name=case_name,
            submission_type=_submission_type,
            bonus_pct=_bonus_pct,
            critical_count=_critical_count,
            high_count=_high_count,
            warn_count=_warn_count,
            parking_pass=_parking_pass,
            pii_high_count=_pii_high_count,
        )

        peer_stats = get_peer_stats(_submission_type)

        # Track B AI pipeline — pass regulation year for field validation and
        # the secondary PDF (if present) for cross-document comparison.
        ai_findings = _run_ai_pipeline(
            primary_pdf,
            reg_year=_reg_year_from_version(reg_version),
            secondary_pdf=str(re_path) if re_path else None,
        )

        # Composite confidence scoring — corroborate Track B findings against
        # the Track A rule engine and route low-confidence ones to human review.
        if ai_findings:
            try:
                from .parsing_pipeline.confidence_scorer import score_findings
                ai_findings = score_findings(ai_findings, findings)
            except Exception as exc:
                log.error("Confidence scoring error: %s", exc)

        # PDF annotation
        _set_task_progress(task_id, "running", "標注 PDF 中…")
        annotated_key: Optional[str] = None
        annotated_s3_key: Optional[str] = None
        pdf_bytes: Optional[bytes] = None
        try:
            annotate_source = primary_pdf
            masked_bytes: Optional[bytes] = None
            if masking_enabled() and any(r.severity == "HIGH" for r in pii_risks):
                masked_bytes = mask_pii(primary_pdf, list(pii_risks))
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                    tmp.write(masked_bytes)
                    annotate_source = tmp.name
            pdf_bytes = annotate_pdf(annotate_source, findings)
            annotated_key = uuid.uuid4().hex[:8]
            _PDF_CACHE[annotated_key] = pdf_bytes
            if case_meta_id and s3_available():
                annotated_s3_key = save_annotated_pdf(case_meta_id, pdf_bytes)
        except Exception:
            log.exception("PDF annotation/masking failed")
        finally:
            if masked_bytes and annotate_source != primary_pdf:
                try:
                    os.unlink(annotate_source)
                except OSError:
                    pass

        # Build report
        documents = [bp_fname]
        if re_path:
            documents.append(re_fname or "權利變換計畫報告書.pdf")

        # 來源標籤須與版本選擇的優先序一致
        if review_filing_date:
            rd_source = "審議資料表（辦理過程報核日）"
            rd_page = review_table.raw_page if review_table else None
        elif report_date_roc and front_docs:
            rd_source = front_docs.report_date_source
            rd_page = front_docs.report_date_page
        elif fill_date_fallback:
            rd_source = "審議資料表（填表日期）"
            rd_page = review_table.raw_page if review_table else None
        elif filename_date_fallback:
            rd_source = "檔名日期（自動辨識）"
            rd_page = None
        else:
            rd_source = None
            rd_page = None

        # 問題頁標註截圖：把已標紅框的頁 raster 成內嵌圖，審核者零點擊看到問題位置。
        # 純地端光柵化，失敗回空 dict（報告降級成純文字頁碼跳轉，不中斷）。
        evidence_images: dict[int, str] = {}
        if pdf_bytes:
            evidence_pages = {p for p in (_evidence_page(f.evidence) for f in findings) if p}
            if rd_page:
                evidence_pages.add(rd_page)
            evidence_images = render_evidence_thumbnails(pdf_bytes, evidence_pages)

        report = AuditReport(
            case_name=case_name,
            audit_time=audit_time,
            rule_version=reg_version.label,
            documents=documents,
            review_table=review_table,
            front_docs=front_docs,
            pii_risks=pii_risks,
            term_matches=list(term_matches),
            findings=findings,
            fill_date_iso=fill_date_iso,
            report_date=version_date,
            report_date_source=rd_source,
            report_date_page=rd_page,
            report_date_secondary=report_date_secondary,
            report_date_secondary_source=report_date_secondary_source,
            diffs=diffs,
            prev_audit_time=prev_audit_time,
            annotated_pdf_key=annotated_key,
            evidence_images=evidence_images,
            ai_findings=ai_findings,
            peer_stats=peer_stats,
        )

        _set_task_progress(task_id, "running", "產生審查報告中…")
        html = generate_report(report)

        # Save S3 meta
        if bp_s3_key and s3_available():
            try:
                save_case_meta(
                    bp_key=bp_s3_key,
                    bp_filename=bp_fname,
                    case_name=case_name,
                    meta_id=case_meta_id,
                    re_key=re_s3_key,
                    re_filename=re_fname if re_s3_key else None,
                    annotated_key=annotated_s3_key,
                )
            except Exception as exc:
                log.warning("S3 case-meta save failed (non-fatal): %s", exc)

        with _TASKS_LOCK:
            _AUDIT_TASKS[task_id]["status"] = "done"
            _AUDIT_TASKS[task_id]["progress"] = "完成"
            _AUDIT_TASKS[task_id]["html"] = html
            _AUDIT_TASKS[task_id]["done_at"] = time.time()

    except Exception as exc:
        log.exception("Async audit task %s failed", task_id)
        with _TASKS_LOCK:
            _AUDIT_TASKS[task_id]["status"] = "error"
            _AUDIT_TASKS[task_id]["progress"] = f"審查失敗：{exc}"
            _AUDIT_TASKS[task_id]["done_at"] = time.time()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

init_db()
_cleaned = delete_test_runs()
if _cleaned:
    log.info("Startup cleanup: removed %d test run(s) from history", _cleaned)
if s3_available():
    setup_bucket_encryption()


@app.get("/health")
async def health() -> JSONResponse:
    """Health check. Also surfaces the active OCR mode so the地端 VLM vs
    PaddleOCR switch (VLM_ENDPOINT) is observable at runtime without SSH."""
    from urllib.parse import urlparse
    from .parsers.vlm_reader import vlm_enabled

    endpoint = os.getenv("VLM_ENDPOINT", "").strip()
    return JSONResponse({
        "status": "ok",
        "ocr_mode": "vlm" if vlm_enabled() else "paddleocr",
        "vlm_endpoint_host": urlparse(endpoint).netloc or None,
    })


@app.get("/", response_class=HTMLResponse)
async def upload_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "upload.html", {"method_docs": METHOD_DOCS}
    )


@app.get("/upload-url")
async def upload_url(filename: str) -> JSONResponse:
    if not s3_available():
        raise HTTPException(status_code=503, detail="S3 未設定")
    return JSONResponse(generate_upload_url(filename))


@app.get("/cases")
async def cases_list() -> JSONResponse:
    if not s3_available():
        return JSONResponse([])
    return JSONResponse(list_cases())


@app.delete("/cases/{meta_id}")
async def delete_case(meta_id: str) -> JSONResponse:
    if not s3_available():
        raise HTTPException(status_code=503, detail="S3 未設定")
    delete_case_meta(meta_id)
    return JSONResponse({"ok": True})


@app.get("/cases/{meta_id}/download")
async def download_annotated(meta_id: str) -> JSONResponse:
    """Return a presigned S3 URL for downloading the annotated PDF."""
    if not s3_available():
        raise HTTPException(status_code=503, detail="S3 未設定")
    key = f"annotated/{meta_id}_annotated.pdf"
    url = generate_download_url(key, f"annotated_{meta_id}.pdf")
    return JSONResponse({"url": url})


@app.get("/wiki", response_class=HTMLResponse)
async def wiki_page(request: Request) -> HTMLResponse:
    data = load_all_wiki()
    return templates.TemplateResponse(request, "wiki.html", {"wiki": data})


# ── 審議資料表標註工具（供驗收人員核對訓練樣本）──────────────────────────────
_LABEL_TOOL_PATH = Path(__file__).parent.parent / "審議資料表_訓練標註" / "標註工具.html"
_LABELS_MEM: Optional[dict] = None  # S3 未設定時的暫存（重新部署會消失）


@app.get("/label", response_class=HTMLResponse)
async def label_tool() -> Response:
    """單一自足 HTML 標註工具（內嵌 14 張圖）。"""
    if not _LABEL_TOOL_PATH.exists():
        raise HTTPException(status_code=404, detail="標註工具尚未產生")
    return HTMLResponse(_LABEL_TOOL_PATH.read_text(encoding="utf-8"))


@app.post("/label/submit")
async def label_submit(request: Request) -> JSONResponse:
    """接收驗收人員修正後的標註，存 S3（或記憶體暫存）供承辦收回。"""
    global _LABELS_MEM
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="請提供 JSON")
    _LABELS_MEM = data
    stored = "memory"
    if s3_available():
        try:
            save_labels(data)
            stored = "s3"
        except Exception:
            log.exception("標註存 S3 失敗（已留記憶體暫存）")
    return JSONResponse({"ok": True, "stored": stored,
                         "reviewed": data.get("reviewed"), "total": data.get("total")})


@app.get("/label/submissions")
async def label_submissions() -> JSONResponse:
    """收回最新提交的標註（承辦／訓練用）。"""
    data = load_labels() if s3_available() else None
    if data is None:
        data = _LABELS_MEM
    if data is None:
        raise HTTPException(status_code=404, detail="尚無提交的標註")
    return JSONResponse(data)


@app.get("/download/{key}")
async def download_annotated(key: str, dl: int = 0) -> Response:
    """標註 PDF：預設 inline（瀏覽器開新頁 + #page=N 直接跳到問題頁，免下載），
    dl=1 才強制 attachment 下載（供「下載標註 PDF」按鈕）。"""
    pdf_bytes = _PDF_CACHE.get(key)
    if not pdf_bytes:
        raise HTTPException(status_code=404, detail="找不到標註 PDF，請重新審查後下載")
    disposition = "attachment" if dl else "inline"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'{disposition}; filename="annotated_{key}.pdf"'},
    )


@app.post("/debug/ocr")
async def debug_ocr(
    pdf: UploadFile = File(...),
    page: int = Form(...),
    zoom: float = Form(3.0),
) -> JSONResponse:
    """診斷：回傳指定頁（1-based）的原始 OCR 偵測（文字＋座標＋信心度）。

    用來查明審議資料表某欄位為何擷取錯誤／未擷取——例如「1,406.00」被 PaddleOCR
    切成幾個 token、座標如何，才能對症調整幾何重建。純地端 OCR，無外部呼叫。
    """
    if not (pdf.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="僅支援 PDF")
    from .parsers.ocr_reader import ocr_available, ocr_page_boxes
    if not ocr_available():
        raise HTTPException(status_code=503, detail="OCR 未安裝（PaddleOCR 不可用）")
    data = await pdf.read()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        dets = ocr_page_boxes(tmp_path, page - 1, zoom=max(1.0, min(zoom, 8.0)))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"OCR 失敗：{exc}")
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    out = [
        {
            "text": d.get("text"),
            "conf": round(d.get("conf", 0.0), 3),
            "x0": round(d.get("x0", 0.0), 1),
            "x1": round(d.get("x1", 0.0), 1),
            "yc": round(d.get("yc", 0.0), 1),
            "h": round(d.get("h", 0.0), 1),
        }
        for d in dets[:800]
    ]
    return JSONResponse({"page": page, "count": len(dets), "detections": out})


@app.post("/audit")
async def audit_submit(
    request: Request,
    business_plan: Optional[UploadFile] = File(None),
    rights_exchange: Optional[UploadFile] = File(None),
    business_plan_key: Optional[str] = Form(None),
    rights_exchange_key: Optional[str] = Form(None),
) -> JSONResponse:
    """Accept audit request and return task_id immediately (async processing)."""
    _prune_old_tasks()
    has_direct = business_plan and business_plan.filename
    has_s3 = bool(business_plan_key)
    if not has_direct and not has_s3:
        raise HTTPException(status_code=400, detail="請上傳事業計畫報告書（PDF）")

    # Read file bytes now (async context) before handing off to thread
    bp_bytes: Optional[bytes] = None
    bp_fname: Optional[str] = None
    re_bytes: Optional[bytes] = None
    re_fname_direct: Optional[str] = None
    if has_direct:
        if not (business_plan.filename or "").lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="僅支援 PDF 格式")
        bp_bytes = await business_plan.read()
        bp_fname = business_plan.filename
        if rights_exchange and rights_exchange.filename and rights_exchange.filename.lower().endswith(".pdf"):
            re_bytes = await rights_exchange.read()
            re_fname_direct = rights_exchange.filename

    task_id = uuid.uuid4().hex[:16]
    with _TASKS_LOCK:
        _AUDIT_TASKS[task_id] = {"status": "queued", "progress": "排隊中…", "html": None, "done_at": None}

    t = threading.Thread(
        target=_run_audit_sync,
        args=(task_id, business_plan_key, rights_exchange_key, bp_bytes, bp_fname, re_bytes, re_fname_direct),
        daemon=True,
    )
    t.start()
    return JSONResponse({"task_id": task_id})


@app.get("/audit/{task_id}/status")
async def audit_task_status(task_id: str) -> JSONResponse:
    with _TASKS_LOCK:
        task = dict(_AUDIT_TASKS.get(task_id, {}))
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return JSONResponse({"status": task["status"], "progress": task["progress"]})


@app.get("/audit/{task_id}/report", response_class=HTMLResponse)
async def audit_task_report(task_id: str) -> HTMLResponse:
    with _TASKS_LOCK:
        task = dict(_AUDIT_TASKS.get(task_id, {}))
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task["status"] == "error":
        raise HTTPException(status_code=500, detail=task["progress"])
    if task["status"] != "done":
        raise HTTPException(status_code=202, detail=task["progress"])
    return HTMLResponse(task["html"])


def _audit_legacy_compat(
    has_direct: bool,
    has_s3: bool,
    business_plan_key: Optional[str] = None,
    rights_exchange_key: Optional[str] = None,
) -> None:
    """Placeholder — actual logic lives in _run_audit_sync."""
    pass


# ── Keep old sync path for direct form fallback ──────────────────────────────
# (browser without JS hits the classic multipart submit → form action="/audit")
# FastAPI routes are matched in definition order so the POST above wins for
# JSON/fetch clients; this block is intentionally unreachable via fetch.
# If needed later, a /audit/sync endpoint can be added.

def _compute_diff(prev_findings_json: str, curr_findings) -> list[FindingDiff]:
    try:
        prev = {f["rule_id"]: f for f in json.loads(prev_findings_json)}
    except Exception:
        return []

    result = []
    for f in curr_findings:
        p = prev.get(f.rule_id)
        if p is None:
            change, prev_status = "new", None
        elif p["status"] == f.status:
            continue
        elif p["status"] in ("fail", "warn") and f.status == "pass":
            change, prev_status = "improved", p["status"]
        elif p["status"] == "pass" and f.status in ("fail", "warn"):
            change, prev_status = "regressed", p["status"]
        else:
            change, prev_status = "changed", p["status"]

        result.append(FindingDiff(
            rule_id=f.rule_id,
            rule_name=f.rule_name,
            change=change,
            prev_status=prev_status,
            curr_status=f.status,
            message=f.message,
        ))

    return result
