from __future__ import annotations

import json
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates

from .annotator import annotate_pdf
from .extractors.front_docs import extract_front_docs
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
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
_engine = build_default_engine()

# In-memory cache for annotated PDFs (key → bytes)
_PDF_CACHE: dict[str, bytes] = {}

init_db()


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({
        "status": "ok",
        "templates_dir": str(_TEMPLATES_DIR),
        "templates_exists": _TEMPLATES_DIR.exists(),
        "upload_html_exists": (_TEMPLATES_DIR / "upload.html").exists(),
    })


@app.get("/", response_class=HTMLResponse)
async def upload_page(request: Request) -> HTMLResponse:
    try:
        return templates.TemplateResponse("upload.html", {"request": request})
    except Exception as exc:
        import traceback
        return HTMLResponse(f"<pre>ERROR: {exc}\n{traceback.format_exc()}</pre>", status_code=500)


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
    business_plan: Optional[UploadFile] = File(None),
    rights_exchange: Optional[UploadFile] = File(None),
) -> HTMLResponse:
    if not business_plan or not business_plan.filename:
        raise HTTPException(status_code=400, detail="請上傳事業計畫報告書（PDF）")
    if not business_plan.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="僅支援 PDF 格式")

    with tempfile.TemporaryDirectory() as tmp_dir:
        # Save primary PDF
        bp_path = Path(tmp_dir) / (business_plan.filename or "business_plan.pdf")
        bp_path.write_bytes(await business_plan.read())
        primary_pdf = str(bp_path)

        # Save secondary PDF if provided
        re_path: Optional[Path] = None
        if rights_exchange and rights_exchange.filename and rights_exchange.filename.lower().endswith(".pdf"):
            re_path = Path(tmp_dir) / (rights_exchange.filename or "rights_exchange.pdf")
            re_path.write_bytes(await rights_exchange.read())

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

        # --- Version selection (based on fill_date from review table) ---
        fill_date = review_table.fill_date if review_table else None
        reg_version, fill_date_iso = select_version(fill_date)

        # --- Case name & audit metadata ---
        case_name = (
            review_table.case_name
            if review_table and review_table.case_name
            else business_plan.filename
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
        try:
            pdf_bytes = annotate_pdf(primary_pdf, findings)
            annotated_key = uuid.uuid4().hex[:8]
            _PDF_CACHE[annotated_key] = pdf_bytes
        except Exception:
            pass

        # --- Build document list ---
        documents = [business_plan.filename]
        if re_path:
            documents.append(rights_exchange.filename or "權利變換計畫報告書.pdf")

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
            diffs=diffs,
            prev_audit_time=prev_audit_time,
            annotated_pdf_key=annotated_key,
        )

        return HTMLResponse(content=generate_report(report))


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
