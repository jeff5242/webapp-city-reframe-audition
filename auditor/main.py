from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from .extractors.front_docs import extract_front_docs
from .extractors.review_table import extract_review_table
from .extractors.term_checker import extract_number_contexts, scan_for_wrong_terms
from .models import AuditData, AuditReport
from .reporters.html_reporter import generate_report
from .rules.engine import build_default_engine
from .version_selector import select_version

app = FastAPI(
    title="臺北市都市更新審議自動審查",
    description="111年版規則 POC",
    version="0.1.0",
)

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
_engine = build_default_engine()


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.get("/", response_class=HTMLResponse)
async def upload_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("upload.html", {"request": request})


@app.post("/audit", response_class=HTMLResponse)
async def audit(
    request: Request,
    files: list[UploadFile] = File(...),
) -> HTMLResponse:
    if not files or all(f.filename == "" for f in files):
        raise HTTPException(status_code=400, detail="請上傳至少一個 PDF 檔案")

    pdf_files = [f for f in files if f.filename and f.filename.lower().endswith(".pdf")]
    if not pdf_files:
        raise HTTPException(status_code=400, detail="僅支援 PDF 格式")

    with tempfile.TemporaryDirectory() as tmp_dir:
        saved: list[Path] = []
        for upload in pdf_files:
            dest = Path(tmp_dir) / (upload.filename or "upload.pdf")
            dest.write_bytes(await upload.read())
            saved.append(dest)

        primary_pdf = str(saved[0])

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

        case_name = (
            review_table.case_name
            if review_table and review_table.case_name
            else pdf_files[0].filename or "未知案件"
        )

        fill_date = review_table.fill_date if review_table else None
        reg_version, _ = select_version(fill_date)

        report = AuditReport(
            case_name=case_name,
            audit_time=datetime.now().strftime("%Y-%m-%d %H:%M"),
            rule_version=reg_version.label,
            documents=[f.filename or "" for f in pdf_files],
            review_table=review_table,
            front_docs=front_docs,
            pii_risks=pii_risks,
            term_matches=list(term_matches),
            findings=findings,
        )

        return HTMLResponse(content=generate_report(report))
