"""
E2E tests for the FastAPI application — covers upload-url endpoint,
S3 presigned upload flow, and audit via S3 key.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

load_dotenv()

from auditor.main import app  # noqa: E402

client = TestClient(app)

_SAMPLE_PDF = Path(__file__).parent.parent / "事業計劃報告書及權利變更計劃書.zip"

skip_if_no_creds = pytest.mark.skipif(
    not all([
        os.getenv("AWS_ACCESS_KEY_ID"),
        os.getenv("AWS_SECRET_ACCESS_KEY"),
        os.getenv("S3_BUCKET"),
    ]),
    reason="AWS credentials not configured",
)


# ── health ───────────────────────────────────────────────────────────────────

def test_health(monkeypatch):
    monkeypatch.delenv("VLM_ENDPOINT", raising=False)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["ocr_mode"] == "paddleocr"      # no VLM_ENDPOINT → PaddleOCR
    assert body["vlm_endpoint_host"] is None


def test_health_reflects_vlm_switch(monkeypatch):
    monkeypatch.setenv("VLM_ENDPOINT", "https://gpu.example.com:8000")
    body = client.get("/health").json()
    assert body["ocr_mode"] == "vlm"
    assert body["vlm_endpoint_host"] == "gpu.example.com:8000"


def test_looks_like_test_upload():
    from auditor.main import _looks_like_test_upload
    assert _looks_like_test_upload("e2e test.pdf", "x") is True
    assert _looks_like_test_upload("api_test.pdf", "x") is True
    assert _looks_like_test_upload("plan.pdf", "測試案件") is True      # 案名含測試
    assert _looks_like_test_upload("大魯閣.pdf", "大魯閣-事業計畫") is False


def test_homepage_ocr_badge(monkeypatch):
    monkeypatch.delenv("VLM_ENDPOINT", raising=False)
    assert "PaddleOCR" in client.get("/").text
    monkeypatch.setenv("VLM_ENDPOINT", "https://gpu:8000")
    assert "地端 VLM" in client.get("/").text


# ── homepage ─────────────────────────────────────────────────────────────────

def test_homepage_renders():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "都市更新審議" in resp.text
    assert "開始審查" in resp.text


def test_homepage_has_wiki_link():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "/wiki" in resp.text


def test_homepage_has_s3_notice():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "AWS S3" in resp.text


# ── wiki ─────────────────────────────────────────────────────────────────────

def test_wiki_page_renders():
    resp = client.get("/wiki")
    assert resp.status_code == 200
    assert "法規" in resp.text


# ── upload-url endpoint ───────────────────────────────────────────────────────

@skip_if_no_creds
def test_upload_url_returns_sigv4_presigned_url():
    """
    Regression: /upload-url must return SigV4 URL.
    SigV2 caused mid-upload 網路錯誤 in Tokyo (ap-northeast-1).
    """
    import urllib.parse
    resp = client.get("/upload-url", params={"filename": "test.pdf"})
    assert resp.status_code == 200
    data = resp.json()

    assert "key" in data
    assert "upload_url" in data

    parsed = urllib.parse.urlparse(data["upload_url"])
    params = urllib.parse.parse_qs(parsed.query)
    assert "X-Amz-Credential" in params, (
        "upload-url returned SigV2 instead of SigV4 — will break browser upload"
    )


@skip_if_no_creds
def test_upload_url_key_format():
    resp = client.get("/upload-url", params={"filename": "my report.pdf"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["key"].startswith("uploads/")
    assert " " not in data["key"]
    assert data["expires_in"] == 300


def test_upload_url_returns_503_without_creds(monkeypatch):
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.delenv("S3_BUCKET", raising=False)
    import importlib, auditor.s3 as s3_mod
    importlib.reload(s3_mod)
    import auditor.main as main_mod
    main_mod.s3_available = s3_mod.s3_available

    resp = client.get("/upload-url", params={"filename": "x.pdf"})
    assert resp.status_code == 503


# ── audit via S3 key (E2E) ───────────────────────────────────────────────────

@skip_if_no_creds
def test_audit_via_s3_key(tmp_path):
    """
    Full S3 upload flow: put minimal PDF to S3 via presigned URL,
    then call /audit with the S3 key, verify report HTML returned.
    """
    import requests

    # Minimal valid-looking PDF (just enough for pdfplumber to open)
    minimal_pdf = (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
        b"xref\n0 4\n0000000000 65535 f\n"
        b"0000000009 00000 n\n0000000058 00000 n\n0000000115 00000 n\n"
        b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n190\n%%EOF"
    )

    # Step 1: get presigned URL
    url_resp = client.get("/upload-url", params={"filename": "e2e_test.pdf"})
    assert url_resp.status_code == 200
    url_data = url_resp.json()
    key = url_data["key"]

    # Step 2: upload directly to S3 — include SSE header required by signed URL
    sse_headers = {url_data["sse_header"]: url_data["sse_value"]} if url_data.get("sse_header") else {}
    put_resp = requests.put(
        url_data["upload_url"],
        data=minimal_pdf,
        headers={"Content-Type": "application/pdf", **sse_headers},
        timeout=30,
    )
    assert put_resp.status_code == 200, f"S3 PUT failed: {put_resp.status_code}"

    # Step 3: call /audit with S3 key — now returns task_id immediately
    audit_resp = client.post("/audit", data={"business_plan_key": key})
    assert audit_resp.status_code == 200
    data = audit_resp.json()
    assert "task_id" in data

    # Step 4: poll until done (background thread runs synchronously in TestClient)
    task_id = data["task_id"]
    import time
    for _ in range(60):
        status_resp = client.get(f"/audit/{task_id}/status")
        assert status_resp.status_code == 200
        s = status_resp.json()
        if s["status"] == "done":
            report_resp = client.get(f"/audit/{task_id}/report")
            assert report_resp.status_code == 200
            assert "DOCTYPE" in report_resp.text or "審查報告" in report_resp.text
            return
        if s["status"] == "error":
            raise AssertionError(f"Audit task failed: {s['progress']}")
        time.sleep(2)
    raise AssertionError("Audit task timed out in test")


# ── cases history endpoint ───────────────────────────────────────────────────

def test_cases_endpoint_returns_list():
    resp = client.get("/cases")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@skip_if_no_creds
def test_cases_endpoint_contains_saved_meta():
    from auditor.s3 import save_case_meta
    save_case_meta(
        bp_key="uploads/api_test.pdf",
        bp_filename="api_test.pdf",
        case_name="API 測試案件",
    )
    resp = client.get("/cases")
    assert resp.status_code == 200
    names = [c.get("case_name") for c in resp.json()]
    assert "API 測試案件" in names


# ── audit: direct file upload still works ────────────────────────────────────

def test_audit_missing_file_returns_400():
    resp = client.post("/audit")
    assert resp.status_code == 400


def test_audit_non_pdf_returns_400():
    resp = client.post(
        "/audit",
        files={"business_plan": ("test.txt", b"not a pdf", "text/plain")},
    )
    assert resp.status_code == 400


# ── Track B wiring: reg_year + cross-doc (Phase 1) ────────────────────────────

def test_reg_year_from_version_extracts_year():
    from auditor.main import _reg_year_from_version
    from auditor.version_selector import RegulationVersion

    assert _reg_year_from_version(RegulationVersion("113年版", "2024-12-03", "")) == "113"
    assert _reg_year_from_version(RegulationVersion("111年版", "2022-03-24", "2024-12-02")) == "111"


def test_reg_year_from_version_falls_back_on_bad_input():
    from auditor.main import _reg_year_from_version

    class Broken:
        @property
        def label(self):
            raise ValueError("no label")

    assert _reg_year_from_version(Broken()) == "111"


def test_run_ai_pipeline_returns_empty_without_api_key(monkeypatch):
    from auditor.main import _run_ai_pipeline
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert _run_ai_pipeline("any.pdf") == []


def test_run_ai_pipeline_passes_reg_year_to_field_auditor(monkeypatch):
    """reg_year must be forwarded to extract_and_validate."""
    from unittest.mock import patch, MagicMock
    import auditor.main as main_mod

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    captured = {}

    def fake_extract_and_validate(md, reg_year="111"):
        captured["reg_year"] = reg_year
        return (MagicMock(), [])

    with patch.object(main_mod, "_pdf_to_markdown", return_value="some markdown"), \
         patch("auditor.parsing_pipeline.chunker.chunk_markdown", return_value=["chunk"]), \
         patch("auditor.parsing_pipeline.llm_auditor.audit_chunks", return_value=[]), \
         patch("auditor.parsing_pipeline.field_auditor.extract_and_validate",
               side_effect=fake_extract_and_validate):
        main_mod._run_ai_pipeline("primary.pdf", reg_year="113")

    assert captured.get("reg_year") == "113"


def test_run_ai_pipeline_runs_cross_doc_when_secondary_present(monkeypatch):
    """compare_documents must run and produce cross-source findings."""
    from unittest.mock import patch, MagicMock
    import auditor.main as main_mod
    from auditor.parsing_pipeline.cross_doc_comparator import CrossDocFinding

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    cross_finding = CrossDocFinding(
        field_name="更新單元總面積",
        rule_id="CONS-AREA-001",
        severity="critical",
        business_plan_value="1000.0 m²",
        rights_exchange_value="1200.0 m²",
        reason="面積不一致",
    )

    with patch.object(main_mod, "_pdf_to_markdown", return_value="markdown"), \
         patch("auditor.parsing_pipeline.chunker.chunk_markdown", return_value=["chunk"]), \
         patch("auditor.parsing_pipeline.llm_auditor.audit_chunks", return_value=[]), \
         patch("auditor.parsing_pipeline.field_auditor.extract_and_validate",
               return_value=(MagicMock(), [])), \
         patch("auditor.parsing_pipeline.cross_doc_comparator.compare_documents",
               return_value=[cross_finding]) as mock_compare:
        findings = main_mod._run_ai_pipeline(
            "primary.pdf", reg_year="111", secondary_pdf="secondary.pdf"
        )

    mock_compare.assert_called_once()
    cross = [f for f in findings if f.source == "cross"]
    assert len(cross) == 1
    assert cross[0].rule_id == "CONS-AREA-001"
    assert "1000.0 m²" in cross[0].detected_text
    assert "1200.0 m²" in cross[0].detected_text


def test_run_ai_pipeline_grounds_llm_findings(monkeypatch):
    """LLM findings must carry evidence_verified reflecting the source check."""
    from unittest.mock import patch, MagicMock
    import auditor.main as main_mod
    from auditor.parsing_pipeline.llm_auditor import LlmFinding

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    finding = LlmFinding(
        rule_id="LAW-001",
        error_type="regulatory_violation",
        severity="warning",
        detected_text="容積獎勵合計45%",
        suggested_text="",
        reason="超過上限",
        page_number=3,
    )

    with patch.object(main_mod, "_pdf_to_markdown",
                      return_value="本案容積獎勵合計45%，超過上限。"), \
         patch("auditor.parsing_pipeline.chunker.chunk_markdown", return_value=["chunk"]), \
         patch("auditor.parsing_pipeline.llm_auditor.audit_chunks", return_value=[finding]), \
         patch("auditor.parsing_pipeline.field_auditor.extract_and_validate",
               return_value=(MagicMock(), [])):
        findings = main_mod._run_ai_pipeline("primary.pdf", reg_year="111")

    llm = [f for f in findings if f.source == "llm"]
    assert len(llm) == 1
    assert llm[0].evidence_verified is True
    assert llm[0].evidence_text == "容積獎勵合計45%"


def test_run_ai_pipeline_skips_cross_doc_when_no_secondary(monkeypatch):
    """compare_documents must NOT run when secondary_pdf is None."""
    from unittest.mock import patch, MagicMock
    import auditor.main as main_mod

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    with patch.object(main_mod, "_pdf_to_markdown", return_value="markdown"), \
         patch("auditor.parsing_pipeline.chunker.chunk_markdown", return_value=["chunk"]), \
         patch("auditor.parsing_pipeline.llm_auditor.audit_chunks", return_value=[]), \
         patch("auditor.parsing_pipeline.field_auditor.extract_and_validate",
               return_value=(MagicMock(), [])), \
         patch("auditor.parsing_pipeline.cross_doc_comparator.compare_documents") as mock_compare:
        main_mod._run_ai_pipeline("primary.pdf", reg_year="111", secondary_pdf=None)

    mock_compare.assert_not_called()


# ── startup OCR warmup (Item 5) ───────────────────────────────────────────────

def test_ocr_warmup_called_on_startup():
    """OCR _get_reader must be called in a background thread during app startup."""
    from unittest.mock import patch, MagicMock
    from fastapi.testclient import TestClient
    import auditor.main as main_mod

    call_log: list[str] = []

    def fake_get_reader():
        call_log.append("called")

    with patch("auditor.parsers.ocr_reader._get_reader", side_effect=fake_get_reader):
        with TestClient(main_mod.app) as tc:
            # lifespan runs on __enter__; give warmup thread a moment
            import time as _time
            _time.sleep(0.1)

    assert "called" in call_log, "_get_reader was not invoked during startup warmup"
