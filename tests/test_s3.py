"""
Tests for S3 module — covers the SigV4 regression and presigned URL contract.

WHY SigV4 matters: ap-northeast-1 (Tokyo) rejected SigV2 presigned PUT URLs
mid-upload (browser got 網路錯誤 at ~33%), fixed by Config(signature_version='s3v4').
"""
from __future__ import annotations

import os
import urllib.parse

import pytest
from dotenv import load_dotenv

load_dotenv()


# ── helpers ─────────────────────────────────────────────────────────────────

def _s3_creds_present() -> bool:
    return all([
        os.getenv("AWS_ACCESS_KEY_ID"),
        os.getenv("AWS_SECRET_ACCESS_KEY"),
        os.getenv("S3_BUCKET"),
    ])


skip_if_no_creds = pytest.mark.skipif(
    not _s3_creds_present(),
    reason="AWS credentials not configured",
)


# ── unit: s3_available ───────────────────────────────────────────────────────

def test_s3_available_returns_true_when_env_set(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "FAKE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "FAKE")
    monkeypatch.setenv("S3_BUCKET", "fake-bucket")
    from importlib import reload
    import auditor.s3 as s3_mod
    reload(s3_mod)
    assert s3_mod.s3_available() is True


def test_s3_available_returns_false_when_env_missing(monkeypatch):
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.delenv("S3_BUCKET", raising=False)
    from importlib import reload
    import auditor.s3 as s3_mod
    reload(s3_mod)
    assert s3_mod.s3_available() is False


# ── regression: SigV4 ───────────────────────────────────────────────────────

@skip_if_no_creds
def test_generate_upload_url_uses_sigv4():
    """
    Regression test: presigned URL MUST use SigV4 (X-Amz-Credential in query).
    SigV2 (AWSAccessKeyId) caused mid-upload 網路錯誤 in Tokyo region.
    """
    from auditor.s3 import generate_upload_url
    result = generate_upload_url("test_regression.pdf")
    parsed = urllib.parse.urlparse(result["upload_url"])
    params = urllib.parse.parse_qs(parsed.query)

    assert "X-Amz-Credential" in params, (
        "Presigned URL uses SigV2 (AWSAccessKeyId) instead of SigV4 — "
        "this WILL cause mid-upload failures in ap-northeast-1"
    )
    assert "AWSAccessKeyId" not in params, "SigV2 detected — must use SigV4"


@skip_if_no_creds
def test_generate_upload_url_structure():
    from auditor.s3 import generate_upload_url
    result = generate_upload_url("my document.pdf")

    assert "key" in result
    assert "upload_url" in result
    assert "expires_in" in result

    # Key must be under uploads/ with spaces replaced
    assert result["key"].startswith("uploads/")
    assert " " not in result["key"]

    # URL must point to our bucket over HTTPS
    assert result["upload_url"].startswith("https://")
    assert "urban-renewal-uploads" in result["upload_url"]

    # TTL must be positive
    assert result["expires_in"] > 0


@skip_if_no_creds
def test_generate_upload_url_keys_are_unique():
    from auditor.s3 import generate_upload_url
    r1 = generate_upload_url("same.pdf")
    r2 = generate_upload_url("same.pdf")
    assert r1["key"] != r2["key"], "Each call must produce a unique S3 key"


# ── integration: actual S3 round-trip ───────────────────────────────────────

@skip_if_no_creds
def test_s3_put_and_download_roundtrip(tmp_path):
    """Upload a small blob to S3, download it back, verify content matches."""
    import requests
    from auditor.s3 import download_to_temp, generate_upload_url

    content = b"%PDF-1.4 fake pdf content for test"
    result = generate_upload_url("roundtrip_test.pdf")
    key = result["key"]

    # PUT directly to S3 — must include the SSE header that was signed into the URL
    sse_headers = {result["sse_header"]: result["sse_value"]} if result.get("sse_header") else {}
    resp = requests.put(
        result["upload_url"],
        data=content,
        headers={"Content-Type": "application/pdf", **sse_headers},
        timeout=30,
    )
    assert resp.status_code == 200, f"S3 PUT failed: {resp.status_code} {resp.text}"

    # Download back and verify
    local = download_to_temp(key, str(tmp_path))
    assert open(local, "rb").read() == content


# ── case metadata persistence ─────────────────────────────────────────────────

@skip_if_no_creds
def test_save_case_meta_creates_json_in_s3():
    from auditor.s3 import list_cases, save_case_meta
    meta_key = save_case_meta(
        bp_key="uploads/test_bp.pdf",
        bp_filename="test_bp.pdf",
        case_name="測試案件",
    )
    assert meta_key.startswith("cases/")
    assert meta_key.endswith("_meta.json")

    cases = list_cases()
    ids = [c.get("meta_id") for c in cases]
    meta_id = meta_key.split("/")[1].replace("_meta.json", "")
    assert meta_id in ids, "Saved case not found in list_cases()"


@skip_if_no_creds
def test_list_cases_returns_list():
    from auditor.s3 import list_cases
    cases = list_cases()
    assert isinstance(cases, list)
    if cases:
        c = cases[0]
        assert "case_name" in c
        assert "bp_key" in c
        assert "uploaded_at" in c
