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

def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


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

    # Step 3: call /audit with S3 key
    audit_resp = client.post("/audit", data={"business_plan_key": key})
    assert audit_resp.status_code == 200
    assert "審查報告" in audit_resp.text or "案件名稱" in audit_resp.text or "DOCTYPE" in audit_resp.text
    # File is now kept in S3 as history (not deleted)


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
