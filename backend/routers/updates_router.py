"""
Updates router — admin uploads a .zip desktop build to Backblaze B2 (S3-compatible).

Storage layout in B2:
    desktop-builds/{version}/client-desktop.zip   ← the build bundle
    metadata.json                                  ← current + history manifest

Auth strategy:
    - Dashboard-facing endpoints (list, upload, delete, download) use PIN-based
      cookie auth, matching the web_router pattern (no JWT needed).
    - The /check endpoint is public — polled by the desktop client.
"""
import io
import json
import os
import zipfile
from datetime import datetime, timezone
from typing import Optional

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form, Query, status

router = APIRouter(prefix="/api/admin/updates", tags=["Updates"])

# ── Cookie auth — reuse web_router's verified cookie logic ───────────────────

def _require_web_access(request: Request) -> None:
    """Raise 401 if the PIN cookie is absent or invalid."""
    from backend.routers.web_router import _COOKIE_NAME, _verify_cookie  # noqa: PLC0415
    if not _verify_cookie(request.cookies.get(_COOKIE_NAME)):
        raise HTTPException(status_code=401, detail="Unauthorized — PIN required")

# ── Constants ────────────────────────────────────────────────────────────────

_PRESIGN_TTL_SECONDS: int = 900          # 15 minutes
_METADATA_KEY: str = "metadata.json"
_BUILD_KEY_TEMPLATE: str = "desktop-builds/{version}/client-desktop.zip"
_MAX_HISTORY: int = 20


# ── B2 client factory ─────────────────────────────────────────────────────────

def _get_b2_client():
    """
    Build a boto3 S3 client pointed at the Backblaze B2 S3-compatible endpoint.

    Required environment variables:
        B2_ENDPOINT_URL      — e.g. https://s3.us-west-004.backblazeb2.com
        B2_ACCESS_KEY_ID     — B2 application key ID
        B2_SECRET_ACCESS_KEY — B2 application key secret
        B2_BUCKET_NAME       — target bucket name
    """
    endpoint = os.getenv("B2_ENDPOINT_URL")
    key_id = os.getenv("B2_ACCESS_KEY_ID")
    secret = os.getenv("B2_SECRET_ACCESS_KEY")

    if not all([endpoint, key_id, secret]):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Backblaze B2 credentials are not configured. "
                "Set B2_ENDPOINT_URL, B2_ACCESS_KEY_ID, and B2_SECRET_ACCESS_KEY."
            ),
        )

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=key_id,
        aws_secret_access_key=secret,
        # Force path-style addressing — required by B2
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def _bucket_name() -> str:
    name = os.getenv("B2_BUCKET_NAME", "")
    if not name:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="B2_BUCKET_NAME environment variable is not set.",
        )
    return name


# ── Core B2 helpers ───────────────────────────────────────────────────────────

def upload_desktop_build(file_bytes: bytes, version: str) -> str:
    """
    Upload a desktop build zip to B2.

    Object key: desktop-builds/{version}/client-desktop.zip
    Content-Type is set to application/zip.

    Returns the object key on success.
    Raises HTTPException on credential or network failure.
    """
    client = _get_b2_client()
    bucket = _bucket_name()
    key = _BUILD_KEY_TEMPLATE.format(version=version)

    try:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=file_bytes,
            ContentType="application/zip",
        )
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "unknown")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"B2 upload failed [{error_code}]: {exc}",
        ) from exc
    except BotoCoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"B2 network error during upload: {exc}",
        ) from exc

    return key


def get_client_download_url(version: str) -> str:
    """
    Generate a pre-signed S3 URL for desktop-builds/{version}/client-desktop.zip.

    The URL is valid for exactly 15 minutes (900 seconds).
    Raises HTTPException on credential or network failure.
    """
    client = _get_b2_client()
    bucket = _bucket_name()
    key = _BUILD_KEY_TEMPLATE.format(version=version)

    try:
        url: str = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=_PRESIGN_TTL_SECONDS,
        )
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "unknown")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to generate pre-signed URL [{error_code}]: {exc}",
        ) from exc
    except BotoCoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"B2 network error generating URL: {exc}",
        ) from exc

    return url


# ── Metadata helpers ──────────────────────────────────────────────────────────

def _read_metadata(client, bucket: str) -> dict:
    """Fetch metadata.json from B2; return an empty manifest if not found."""
    try:
        response = client.get_object(Bucket=bucket, Key=_METADATA_KEY)
        return json.loads(response["Body"].read().decode("utf-8"))
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return {
                "version": "",
                "download_url": "",
                "release_notes": "",
                "uploaded_at": "",
                "history": [],
            }
        raise


def _write_metadata(client, bucket: str, meta: dict) -> None:
    """Persist metadata.json to B2."""
    client.put_object(
        Bucket=bucket,
        Key=_METADATA_KEY,
        Body=json.dumps(meta, indent=2).encode("utf-8"),
        ContentType="application/json",
    )


# ── Zip utilities ─────────────────────────────────────────────────────────────

def _extract_version_from_zip(data: bytes) -> str:
    """
    Read the VERSION file from the zip root.
    Falls back to a UTC timestamp string if VERSION is absent or unreadable.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for name in zf.namelist():
                if name.strip("/") == "VERSION" or name.endswith("/VERSION"):
                    return zf.read(name).decode("utf-8").strip()
    except zipfile.BadZipFile:
        pass
    return datetime.now(timezone.utc).strftime("%Y.%m.%d.%H%M")


# ── API endpoints ─────────────────────────────────────────────────────────────

@router.get("")
async def list_updates(request: Request) -> dict:
    """Return the current release manifest (version + history) from B2."""
    _require_web_access(request)
    client = _get_b2_client()
    bucket = _bucket_name()
    try:
        return _read_metadata(client, bucket)
    except (ClientError, BotoCoreError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not fetch metadata from B2: {exc}",
        ) from exc


@router.post("")
async def upload_update(
    request: Request,
    file: UploadFile = File(...),
    release_notes: str = Form(""),
) -> dict:
    """
    Accept a .zip upload, push it to B2, and update the release manifest.
    """
    _require_web_access(request)
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only .zip files are accepted.",
        )

    data: bytes = await file.read()

    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File is not a valid ZIP archive.",
        )

    version: str = _extract_version_from_zip(data)
    object_key: str = upload_desktop_build(data, version)

    # Generate a fresh pre-signed URL for the just-uploaded object
    download_url: str = get_client_download_url(version)

    client = _get_b2_client()
    bucket = _bucket_name()

    try:
        meta: dict = _read_metadata(client, bucket)
        history: list = meta.get("history", [])

        # Archive the previous latest release
        if meta.get("version") and meta["version"] != version:
            history.insert(0, {
                "version": meta["version"],
                "release_notes": meta.get("release_notes", ""),
                "uploaded_at": meta.get("uploaded_at", ""),
            })

        meta.update({
            "version": version,
            "download_url": download_url,
            "release_notes": release_notes,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
            "history": history[:_MAX_HISTORY],
        })

        _write_metadata(client, bucket, meta)
    except (ClientError, BotoCoreError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Metadata update failed: {exc}",
        ) from exc

    return {
        "ok": True,
        "version": version,
        "object_key": object_key,
        "download_url": download_url,
        "size_bytes": len(data),
    }


@router.get("/check")
async def check_for_update(
    version: Optional[str] = Query(None, description="Current client version"),
) -> dict:
    """
    Public endpoint polled by desktop clients.

    Returns whether an update is available. If one is, a fresh 15-minute
    pre-signed download URL is generated on the fly — no stored URL is exposed.
    """
    client = _get_b2_client()
    bucket = _bucket_name()

    try:
        meta: dict = _read_metadata(client, bucket)
    except (ClientError, BotoCoreError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not fetch update metadata: {exc}",
        ) from exc

    latest: str = meta.get("version", "")
    available: bool = bool(latest) and latest != version

    response: dict = {
        "available": available,
        "latest_version": latest,
        "current_version": version or "",
        "release_notes": meta.get("release_notes", "") if available else "",
    }

    if available:
        response["download_url"] = get_client_download_url(latest)

    return response


@router.get("/download/{version}")
async def get_download_url(
    version: str,
    request: Request,
) -> dict:
    """
    Regenerate a 15-minute pre-signed download URL for any specific release version.
    """
    _require_web_access(request)
    url: str = get_client_download_url(version)
    return {
        "version": version,
        "download_url": url,
        "expires_in_seconds": _PRESIGN_TTL_SECONDS,
    }


@router.delete("/{version}")
async def delete_update(
    version: str,
    request: Request,
) -> dict:
    """
    Delete a release from B2 and remove it from the manifest.
    """
    _require_web_access(request)
    client = _get_b2_client()
    bucket = _bucket_name()
    key = _BUILD_KEY_TEMPLATE.format(version=version)

    try:
        client.delete_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "unknown")
        # Tolerate 404 — object may already be gone
        if error_code not in ("NoSuchKey", "404"):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"B2 delete failed [{error_code}]: {exc}",
            ) from exc
    except BotoCoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"B2 network error during delete: {exc}",
        ) from exc

    try:
        meta: dict = _read_metadata(client, bucket)

        if meta.get("version") == version:
            meta["version"] = ""
            meta["download_url"] = ""
            meta["release_notes"] = ""
            meta["uploaded_at"] = ""

        meta["history"] = [
            h for h in meta.get("history", []) if h.get("version") != version
        ]

        _write_metadata(client, bucket, meta)
    except (ClientError, BotoCoreError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Metadata update failed after delete: {exc}",
        ) from exc

    return {"ok": True, "deleted_version": version}
