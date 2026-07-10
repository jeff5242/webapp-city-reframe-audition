from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

_BUCKET = os.getenv("S3_BUCKET", "urban-renewal-uploads")
_REGION = os.getenv("AWS_REGION", "ap-northeast-1")
_PRESIGN_TTL = 300  # seconds


def _client():
    # endpoint_url forces the presigned URL host to match the SigV4 canonical
    # request host. Without it, boto3 signs with s3-{region}.amazonaws.com but
    # generates URLs with s3.amazonaws.com → SignatureDoesNotMatch (403).
    return boto3.client(
        "s3",
        region_name=_REGION,
        endpoint_url=f"https://s3.{_REGION}.amazonaws.com",
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        config=Config(signature_version="s3v4"),
    )


def setup_bucket_encryption() -> None:
    """Enable SSE-S3 (AES-256) as the bucket default — idempotent, safe to call at startup."""
    try:
        _client().put_bucket_encryption(
            Bucket=_BUCKET,
            ServerSideEncryptionConfiguration={
                "Rules": [{
                    "ApplyServerSideEncryptionByDefault": {
                        "SSEAlgorithm": "AES256",
                    },
                    "BucketKeyEnabled": True,
                }]
            },
        )
    except (BotoCoreError, ClientError):
        pass  # Non-fatal: presigned URL still carries the SSE header


def generate_upload_url(filename: str) -> dict:
    """Return a presigned PUT URL and the S3 key for a direct browser upload.

    The signed URL includes the x-amz-server-side-encryption header so S3
    encrypts the object with AES-256 (SSE-S3) on write.  The browser must
    include this header in the PUT request; it is returned here so the
    frontend can set it without hardcoding.
    """
    safe_name = Path(filename).name.replace(" ", "_")
    key = f"uploads/{uuid.uuid4().hex[:8]}_{safe_name}"
    url = _client().generate_presigned_url(
        "put_object",
        Params={
            "Bucket": _BUCKET,
            "Key": key,
            "ContentType": "application/pdf",
            "ServerSideEncryption": "AES256",
        },
        ExpiresIn=_PRESIGN_TTL,
    )
    return {
        "key": key,
        "upload_url": url,
        "expires_in": _PRESIGN_TTL,
        "sse_header": "x-amz-server-side-encryption",
        "sse_value": "AES256",
    }


def download_to_temp(key: str, tmp_dir: str) -> str:
    """Download an S3 object to tmp_dir and return the local file path."""
    filename = Path(key).name
    local_path = str(Path(tmp_dir) / filename)
    _client().download_file(_BUCKET, key, local_path)
    return local_path


def _strip_uuid_prefix(filename: str) -> str:
    """Remove the 8-char uuid prefix added by generate_upload_url."""
    import re
    return re.sub(r"^[a-f0-9]{8}_", "", filename).replace("_", " ")


def save_case_meta(
    bp_key: str,
    bp_filename: str,
    case_name: str,
    meta_id: Optional[str] = None,
    re_key: Optional[str] = None,
    re_filename: Optional[str] = None,
    annotated_key: Optional[str] = None,
) -> str:
    """Write a JSON metadata sidecar to cases/ and return the meta key.

    Persists in S3 across container restarts so history survives redeploys.
    """
    if meta_id is None:
        meta_id = uuid.uuid4().hex[:12]
    meta_key = f"cases/{meta_id}_meta.json"
    meta: dict[str, Any] = {
        "meta_id": meta_id,
        "case_name": case_name,
        "bp_key": bp_key,
        "bp_filename": _strip_uuid_prefix(bp_filename),
        "re_key": re_key,
        "re_filename": _strip_uuid_prefix(re_filename) if re_filename else None,
        "annotated_key": annotated_key,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "audit_count": 1,
    }
    _client().put_object(
        Bucket=_BUCKET,
        Key=meta_key,
        Body=json.dumps(meta, ensure_ascii=False).encode(),
        ContentType="application/json",
        ServerSideEncryption="AES256",
    )
    return meta_key


def delete_case_meta(meta_id: str) -> None:
    """Delete a case's metadata JSON from S3 (best-effort)."""
    try:
        _client().delete_object(Bucket=_BUCKET, Key=f"cases/{meta_id}_meta.json")
    except (BotoCoreError, ClientError):
        pass


def save_annotated_pdf(meta_id: str, pdf_bytes: bytes) -> str:
    """Persist annotated PDF to S3 and return its key."""
    key = f"annotated/{meta_id}_annotated.pdf"
    _client().put_object(
        Bucket=_BUCKET,
        Key=key,
        Body=pdf_bytes,
        ContentType="application/pdf",
        ServerSideEncryption="AES256",
    )
    return key


_LABELS_KEY = "annotations/labels_latest.json"


def save_labels(data: dict) -> str:
    """Persist submitted 審議資料表 annotations to S3 (AES-256). Returns the key."""
    import json

    _client().put_object(
        Bucket=_BUCKET,
        Key=_LABELS_KEY,
        Body=json.dumps(data, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
        ServerSideEncryption="AES256",
    )
    return _LABELS_KEY


def load_labels() -> Optional[dict]:
    """Read the latest submitted annotations from S3; None if absent."""
    import json

    try:
        obj = _client().get_object(Bucket=_BUCKET, Key=_LABELS_KEY)
        return json.loads(obj["Body"].read())
    except Exception:
        return None


def generate_download_url(key: str, filename: str) -> str:
    """Return a presigned GET URL valid for 1 hour."""
    return _client().generate_presigned_url(
        "get_object",
        Params={
            "Bucket": _BUCKET,
            "Key": key,
            "ResponseContentDisposition": f'attachment; filename="{filename}"',
        },
        ExpiresIn=3600,
    )


def list_cases(limit: int = 50) -> List[dict]:
    """Return the most recent case metadata records, newest first."""
    try:
        paginator = _client().get_paginator("list_objects_v2")
        objects = []
        for page in paginator.paginate(Bucket=_BUCKET, Prefix="cases/"):
            objects.extend(page.get("Contents", []))
        # Sort by LastModified descending, take top N
        objects.sort(key=lambda o: o["LastModified"], reverse=True)
        objects = objects[:limit]
        cases = []
        for obj in objects:
            try:
                body = _client().get_object(Bucket=_BUCKET, Key=obj["Key"])["Body"].read()
                cases.append(json.loads(body))
            except (BotoCoreError, ClientError):
                continue
        return cases
    except (BotoCoreError, ClientError):
        return []


def s3_available() -> bool:
    """Return True if S3 credentials are configured."""
    return bool(
        os.getenv("AWS_ACCESS_KEY_ID")
        and os.getenv("AWS_SECRET_ACCESS_KEY")
        and os.getenv("S3_BUCKET")
    )
