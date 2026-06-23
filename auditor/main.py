from __future__ import annotations

import json
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .annotator import annotate_pdf
from .extractors.front_docs import extract_front_docs
from .s3 import (
    delete_case_meta,
    download_to_temp,
    generate_download_url,
    generate_upload_url,
    list_cases,
    s3_available,
    save_annotated_pdf,
    save_case_meta,
    setup_bucket_encryption,
)
from .wiki import load_all_wiki
from .extractors.review_table import extract_review_table
from .extractors.term_checker import extract_number_contexts, scan_for_wrong_terms
from .models import AuditData, AuditReport, FindingDiff
from .reporters.html_reporter import generate_report
from .rules.engine import build_default_engine
from .storage.history import get_prev_run, init_db, save_run
from .version_selector import select_version

app = FastAPI(
    title="臺北市都市更新審議自動審查",
    description="111年版規則 POC",
    version="0.2.0",
)

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_STATIC_DIR = Path(__file__).parent.parent / "static"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
_engine = build_default_engine()

# In-memory cache for annotated PDFs (key → bytes)
_PDF_CACHE: dict[str, bytes] = {}

init_db()
if s3_available():
    setup_bucket_encryption()


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.get("/", response_class=HTMLResponse)
async def upload_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "upload.html")


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


@app.get("/download/{key}")
async def download_annotated(key: str) -> Response:
    pdf_bytes = _PDF_CACHE.get(key)
    if not pdf_bytes:
        raise HTTPException(status_code=404, detail="找不到標註 PDF，請重新審查後下載")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="annotated_{key}.pdf"'},
    )


@app.post("/audit", response_class=HTMLResponse)
async def audit(
    request: Request,
    # Direct file upload (local / small files)
    business_plan: Optional[UploadFile] = File(None),
    rights_exchange: Optional[UploadFile] = File(None),
    # S3 key path (large files via presigned URL)
    business_plan_key: Optional[str] = Form(None),
    rights_exchange_key: Optional[str] = Form(None),
) -> HTMLResponse:
    has_direct = business_plan and business_plan.filename
    has_s3 = bool(business_plan_key)
    if not has_direct and not has_s3:
        raise HTTPException(status_code=400, detail="請上傳事業計畫報告書（PDF）")

    bp_s3_key: Optional[str] = business_plan_key
    re_s3_key: Optional[str] = rights_exchange_key
    # Generate meta_id early so annotated PDF and meta share the same ID
    case_meta_id: Optional[str] = uuid.uuid4().hex[:12] if bp_s3_key else None

    with tempfile.TemporaryDirectory() as tmp_dir:
        # Resolve primary PDF path
        if has_s3:
            primary_pdf = download_to_temp(business_plan_key, tmp_dir)
            bp_filename = Path(business_plan_key).name
        else:
            if not business_plan.filename.lower().endswith(".pdf"):
                raise HTTPException(status_code=400, detail="僅支援 PDF 格式")
            bp_path = Path(tmp_dir) / (business_plan.filename or "business_plan.pdf")
            bp_path.write_bytes(await business_plan.read())
            primary_pdf = str(bp_path)
            bp_filename = business_plan.filename

        # Resolve secondary PDF path
        re_path: Optional[Path] = None
        re_filename: Optional[str] = None
        if rights_exchange_key:
            re_local = download_to_temp(rights_exchange_key, tmp_dir)
            re_path = Path(re_local)
            re_filename = Path(rights_exchange_key).name
        elif rights_exchange and rights_exchange.filename and rights_exchange.filename.lower().endswith(".pdf"):
            re_path = Path(tmp_dir) / (rights_exchange.filename or "rights_exchange.pdf")
            re_path.write_bytes(await rights_exchange.read())
            re_filename = rights_exchange.filename

        # --- Extract from primary document ---
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

        audit_data = AuditData(
            review_table=review_table,
            front_docs=front_docs,
            pii_risks=tuple(pii_risks),
            term_matches=tuple(term_matches),
            number_contexts=tuple(number_contexts),
        )

        findings = _engine.evaluate(audit_data)

        # --- Version selection: prefer 報核日期 from 申請書/切結書/委託書 ---
        # Fall back to 填表日期 from 審議資料表 if front-doc date not found
        report_date_roc: Optional[str] = (
            front_docs.report_date if front_docs and front_docs.report_date else None
        )
        fill_date_fallback = review_table.fill_date if review_table else None
        version_date = report_date_roc or fill_date_fallback
        reg_version, fill_date_iso = select_version(version_date)

        # --- Case name & audit metadata ---
        case_name = (
            review_table.case_name
            if review_table and review_table.case_name
            else bp_filename
        )
        audit_time = datetime.now().strftime("%Y-%m-%d %H:%M")

        # --- Diff computation (compare with previous audit of same case) ---
        diffs: list[FindingDiff] = []
        prev_audit_time: Optional[str] = None
        prev_run = get_prev_run(case_name)
        if prev_run:
            prev_audit_time = prev_run["audit_time"]
            diffs = _compute_diff(prev_run["findings"], findings)

        # --- Save this run to history ---
        save_run(
            case_name=case_name,
            audit_time=audit_time,
            rule_ver=reg_version.label,
            findings_json=json.dumps([
                {
                    "rule_id": f.rule_id,
                    "rule_name": f.rule_name,
                    "status": f.status,
                    "message": f.message,
                    "evidence": f.evidence,
                }
                for f in findings
            ]),
        )

        # --- PDF annotation ---
        annotated_key: Optional[str] = None
        annotated_s3_key: Optional[str] = None
        try:
            pdf_bytes = annotate_pdf(primary_pdf, findings)
            annotated_key = uuid.uuid4().hex[:8]
            _PDF_CACHE[annotated_key] = pdf_bytes
            # Persist to S3 if this is an S3-sourced audit
            if case_meta_id and s3_available():
                annotated_s3_key = save_annotated_pdf(case_meta_id, pdf_bytes)
        except Exception:
            pass

        # --- Build document list ---
        documents = [bp_filename]
        if re_path:
            documents.append(re_filename or "權利變換計畫報告書.pdf")

        # Determine report_date display info
        if report_date_roc and front_docs:
            rd_source = front_docs.report_date_source
            rd_page   = front_docs.report_date_page
        else:
            rd_source = "審議資料表（填表日期）"
            rd_page   = review_table.raw_page if review_table else None

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
            diffs=diffs,
            prev_audit_time=prev_audit_time,
            annotated_pdf_key=annotated_key,
        )

        html = generate_report(report)

    # Persist case metadata in S3 for history (files are kept, not deleted)
    if bp_s3_key and s3_available():
        save_case_meta(
            bp_key=bp_s3_key,
            bp_filename=bp_filename,
            case_name=case_name,
            meta_id=case_meta_id,
            re_key=re_s3_key,
            re_filename=re_filename if re_s3_key else None,
            annotated_key=annotated_s3_key,
        )

    return HTMLResponse(content=html)


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
