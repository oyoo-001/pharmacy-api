"""
Mobile Scan → Medicine router.

Endpoints
---------
POST /api/medicine/submit-scan
    Accepts a scanned drug name from the /addmedicine webpage.
    Looks up the name on openFDA, builds a Medicine row, marks session completed.

GET  /api/sync/check/<token>
    Polled by the desktop UI every few seconds.
    Returns the full medicine dict when session status = 'completed'.

GET  /api/sync/session
    Called by the /addmedicine webpage on load to get (or create) its session token.
    Requires a valid JWT in the Authorization header — the desktop app passes this
    through the URL as  /addmedicine?token=<jwt>  so the page can call this endpoint.
"""
import uuid
import secrets
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.database import get_db
from backend.models import Medicine, MobileSyncSession, User
from backend.auth import require_profile_complete, get_tenant_id, get_current_user
from backend.config import log, GEMINI_API_KEY, GEMINI_MODEL

router = APIRouter(tags=["Mobile Scan"])

_FDA_URL = (
    "https://api.fda.gov/drug/label.json"
    "?search=openfda.brand_name:\"{q}\"+OR+openfda.generic_name:\"{q}\""
    "&limit=1"
)


# ── Schemas ───────────────────────────────────────────────────────────────────

class ScanPayload(BaseModel):
    session_token: str
    pharmacy_id:   str        # admin_id (UUID string)
    extracted_text: str
    # Optional fields — filled by AI or user on the web form
    category:      Optional[str]  = None
    description:   Optional[str]  = None
    batch_number:  Optional[str]  = None
    expiry_date:   Optional[str]  = None
    buying_price:  Optional[float] = None
    selling_price: Optional[float] = None
    quantity:      Optional[int]   = None
    reorder_level: Optional[int]   = None


class ImageAnalysisPayload(BaseModel):
    image_base64: str         # base64-encoded image (no data: prefix)
    mime_type: str = "image/jpeg"
    image_number: int = 1     # which image (1-3) in a multi-image scan


class ScanResponse(BaseModel):
    ok:           bool
    medicine_id:  Optional[str] = None
    message:      str


class ImageAnalysisResponse(BaseModel):
    ok:          bool
    name:        str  = ""
    category:    str  = ""
    description: str  = ""
    batch_number: str  = ""
    expiry_date: str  = ""
    buying_price: float = 0.0
    selling_price: float = 0.0
    quantity:     int  = 0
    reorder_level: int = 10
    error:        str  = ""


class SyncStatusResponse(BaseModel):
    status:       str           # pending | completed | failed
    medicine:     Optional[dict] = None
    error:        Optional[str] = None


# ── openFDA helper ────────────────────────────────────────────────────────────

async def _lookup_fda(drug_name: str) -> dict:
    """
    Query openFDA for basic drug label info.
    Returns a dict of whatever we could extract, or {} on error/miss.
    """
    url = _FDA_URL.format(q=drug_name.strip().replace('"', ''))
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            data = resp.json()
    except Exception as exc:
        log.warning("openFDA lookup failed for %r: %s", drug_name, exc)
        return {}

    results = data.get("results")
    if not results:
        return {}

    hit    = results[0]
    openfda = hit.get("openfda", {})

    # ── name ──────────────────────────────────────────────────────────────────
    name = (
        _first(openfda.get("brand_name"))
        or _first(openfda.get("generic_name"))
        or drug_name
    )

    # ── category ─────────────────────────────────────────────────────────────
    category = (
        _first(openfda.get("pharmaceutical_class_epc"))
        or _first(openfda.get("pharmaceutical_class_cs"))
        or _first(openfda.get("pharm_class_epc"))
        or None
    )
    # Trim long FDA class strings like "Tetracycline-class Antimicrobial [EPC]"
    if category and len(category) > 100:
        category = category[:100]

    # ── description ───────────────────────────────────────────────────────────
    description_parts = (
        hit.get("description")
        or hit.get("purpose")
        or hit.get("indications_and_usage")
    )
    if isinstance(description_parts, list):
        description_parts = " ".join(description_parts)
    description = (description_parts or "")[:2000] or None

    return {
        "name":        name,
        "category":    category,
        "description": description,
    }


def _first(lst) -> Optional[str]:
    """Return the first element of a list, or None."""
    if isinstance(lst, list) and lst:
        return str(lst[0])
    return None


def _safe_float(val) -> float:
    """Convert a value to float safely, stripping non-numeric chars."""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    import re
    cleaned = re.sub(r"[^\d.]", "", str(val))
    try:
        return float(cleaned) if cleaned else 0.0
    except (ValueError, TypeError):
        return 0.0


# ── Gemini image analysis ────────────────────────────────────────────────────

_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/"
    f"models/{GEMINI_MODEL}:generateContent"
)

_ANALYSIS_PROMPT = """\
You are a pharmaceutical data extractor. Analyze this image of a medicine
package, label, or blister pack and extract as much information as possible.

This may be one of up to 3 images of the SAME medicine taken from different
angles (e.g. front name, side batch number, back expiry date). Extract
whatever is visible in THIS particular image — even partial info is valuable.

Return ONLY a valid JSON object with these fields (use empty string / 0 for
anything you cannot determine from THIS image):

{
  "name":         "string — medicine brand or generic name",
  "category":     "string — therapeutic category, e.g. Antibiotics, Analgesic",
  "description":  "string — short description of what the medicine is for",
  "batch_number": "string — batch/lot number if visible",
  "expiry_date":  "string — expiry date in YYYY-MM-DD format if visible",
  "buying_price": "string - possible buying price in kenya",
  "selling_price":"string - possible selling price in kekya with aim of making profit",
  "quantity":     10,
  "reorder_level": 10
}


Do NOT include any markdown, backticks, or explanation — just the raw JSON.
"""


async def _analyze_image_gemini(image_b64: str, mime_type: str) -> dict:
    """Send an image to Gemini and return the extracted medicine fields."""
    import json as _json

    if not GEMINI_API_KEY:
        return {"error": "Gemini API key is not configured on the server."}

    payload = {
        "contents": [{
            "parts": [
                {"text": _ANALYSIS_PROMPT},
                {"inline_data": {"mime_type": mime_type, "data": image_b64}},
            ]
        }],
        "generationConfig": {"temperature": 0.1},
    }

    url = f"{_GEMINI_URL}?key={GEMINI_API_KEY}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json=payload)
            body = resp.json()
    except Exception as exc:
        log.warning("Gemini API call failed: %s", exc)
        return {"error": f"Gemini API request failed: {exc}"}

    if resp.status_code != 200:
        detail = body.get("error", {}).get("message", resp.text[:200])
        log.warning("Gemini returned %s: %s", resp.status_code, detail)
        return {"error": f"Gemini error ({resp.status_code}): {detail}"}

    # Extract the text response
    try:
        text = body["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        return {"error": "Unexpected Gemini response format."}

    # Strip markdown fences if present
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    try:
        return _json.loads(text)
    except _json.JSONDecodeError:
        return {"error": f"Could not parse Gemini response as JSON: {text[:200]}"}


@router.post("/api/medicine/analyze-image", response_model=ImageAnalysisResponse)
async def analyze_image(payload: ImageAnalysisPayload):
    """
    Accept a base64 image from the /addmedicine page, send it to Gemini AI,
    and return structured medicine data for form auto-fill.
    """
    data = await _analyze_image_gemini(payload.image_base64, payload.mime_type)

    if "error" in data:
        return ImageAnalysisResponse(ok=False, error=data["error"])

    return ImageAnalysisResponse(
        ok=True,
        name=str(data.get("name", "")),
        category=str(data.get("category", "")),
        description=str(data.get("description", "")),
        batch_number=str(data.get("batch_number", "")),
        expiry_date=str(data.get("expiry_date", "")),
        buying_price=_safe_float(data.get("buying_price", 0)),
        selling_price=_safe_float(data.get("selling_price", 0)),
        quantity=int(data.get("quantity", 0) or 0),
        reorder_level=int(data.get("reorder_level", 10) or 10),
    )


# ── Session endpoint — called by the /addmedicine page on load ────────────────

@router.get("/api/sync/session")
async def get_or_create_session(
    user: User = Depends(require_profile_complete),
    db:   AsyncSession = Depends(get_db),
):
    """
    Create a fresh MobileSyncSession for this admin and return the token.
    The /addmedicine page calls this once on load, stores the token, and
    includes it in every submit-scan POST.
    """
    admin_id = get_tenant_id(user)
    token    = secrets.token_urlsafe(32)

    session  = MobileSyncSession(
        token    = token,
        admin_id = admin_id,
        status   = "pending",
    )
    db.add(session)
    await db.commit()

    return {
        "token":      token,
        "admin_id":   str(admin_id),
        "expires_in": 3600,
    }


# ── Submit scan ───────────────────────────────────────────────────────────────

@router.post("/api/medicine/submit-scan", response_model=ScanResponse)
async def submit_scan(
    payload: ScanPayload,
    db:      AsyncSession = Depends(get_db),
):
    """
    Receive a scanned drug name from the /addmedicine webpage.

    1. Validate the session token against the claimed pharmacy_id.
    2. Look up the name on openFDA.
    3. Build a Medicine row with sensible defaults for unknown fields.
    4. Mark the session 'completed' with the new medicine_id.
    """
    # ── 1. Validate session ───────────────────────────────────────────────────
    result = await db.execute(
        select(MobileSyncSession).where(
            MobileSyncSession.token == payload.session_token
        )
    )
    sync_session = result.scalar_one_or_none()

    if not sync_session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session token.",
        )

    # Ensure the session belongs to the stated pharmacy
    try:
        claimed_admin = uuid.UUID(payload.pharmacy_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid pharmacy_id format.")

    if sync_session.admin_id != claimed_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Session token does not belong to this pharmacy.",
        )

    # Allow scanning if session is pending OR completed (reusable for 24h)
    if sync_session.status not in ("pending", "completed"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session is {sync_session.status} and cannot accept new scans.",
        )

    # Reject if session is older than 24 hours
    from datetime import timedelta
    session_age = datetime.now(timezone.utc) - sync_session.created_at.replace(tzinfo=timezone.utc)
    if session_age > timedelta(hours=24):
        sync_session.status = "expired"
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Session expired. Please refresh the page to start a new session.",
        )

    # ── 2. openFDA lookup ─────────────────────────────────────────────────────
    drug_text = payload.extracted_text.strip()
    fda_data  = await _lookup_fda(drug_text)

    # Use AI/form values first, fall back to FDA, then defaults
    name        = payload.extracted_text.strip() or fda_data.get("name") or drug_text
    category    = payload.category     or fda_data.get("category")    or None
    description = payload.description  or fda_data.get("description") or None

    log.info("Scan: pharmacy=%s drug=%r → name=%r category=%r",
             claimed_admin, drug_text, name, category)

    # ── 3. Build Medicine row ─────────────────────────────────────────────────
    from datetime import date as _date
    expiry = None
    if payload.expiry_date:
        try:
            expiry = _date.fromisoformat(payload.expiry_date)
        except (ValueError, TypeError):
            pass

    medicine = Medicine(
        admin_id      = claimed_admin,
        session_token = payload.session_token,
        name          = name,
        category      = category,
        description   = description,
        batch_number  = payload.batch_number or None,
        expiry_date   = expiry,
        buying_price  = payload.buying_price  if payload.buying_price  is not None else 0.0,
        selling_price = payload.selling_price if payload.selling_price is not None else 0.0,
        quantity      = payload.quantity      if payload.quantity      is not None else 0,
        reorder_level = payload.reorder_level if payload.reorder_level is not None else 10,
        is_active     = True,
    )
    db.add(medicine)
    await db.flush()   # get medicine.id without full commit yet

    # ── 4. Create notification ──────────────────────────────────────────────
    from backend.routers.notifications_router import create_notification
    await create_notification(
        db, claimed_admin,
        title="New Medicine Added",
        message=f"'{name}' was added via mobile scan.",
        ntype="medicine",
    )

    # ── 5. Mark session completed ─────────────────────────────────────────────
    sync_session.status      = "completed"
    sync_session.medicine_id = medicine.id
    sync_session.updated_at  = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(medicine)

    return ScanResponse(
        ok          = True,
        medicine_id = str(medicine.id),
        message     = f"Medicine '{name}' added successfully.",
    )


@router.post("/api/sync/reset/{token}")
async def reset_sync_session(
    token: str,
    user:  User = Depends(get_current_user),
    db:    AsyncSession = Depends(get_db),
):
    """Reset a session back to pending so the phone can scan another item."""
    result = await db.execute(
        select(MobileSyncSession).where(MobileSyncSession.token == token)
    )
    sync_session = result.scalar_one_or_none()
    if not sync_session:
        raise HTTPException(status_code=404, detail="Session not found.")

    admin_id = get_tenant_id(user)
    if sync_session.admin_id != admin_id:
        raise HTTPException(status_code=403, detail="Access denied.")

    sync_session.status = "pending"
    sync_session.medicine_id = None
    sync_session.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"ok": True, "message": "Session reset."}


# ── Desktop poll endpoint ─────────────────────────────────────────────────────

@router.get("/api/sync/check/{token}", response_model=SyncStatusResponse)
async def check_sync_status(
    token: str,
    user:  User = Depends(get_current_user),
    db:    AsyncSession = Depends(get_db),
):
    """
    Polled by the desktop UI every 3–5 seconds after opening the /addmedicine page.

    Returns:
      {"status": "pending"}                         — still waiting
      {"status": "completed", "medicine": {...}}    — ready; populate the form
      {"status": "failed",    "error": "..."}       — something went wrong
    """
    result = await db.execute(
        select(MobileSyncSession).where(MobileSyncSession.token == token)
    )
    sync_session = result.scalar_one_or_none()

    if not sync_session:
        raise HTTPException(status_code=404, detail="Session not found.")

    # Security: only the owning admin can poll
    admin_id = get_tenant_id(user)
    if sync_session.admin_id != admin_id:
        raise HTTPException(status_code=403, detail="Access denied.")

    if sync_session.status == "pending":
        return SyncStatusResponse(status="pending")

    if sync_session.status == "failed":
        return SyncStatusResponse(
            status="failed",
            error=sync_session.error_message or "Scan failed.",
        )

    # completed — fetch the medicine record
    med_result = await db.execute(
        select(Medicine).where(Medicine.id == sync_session.medicine_id)
    )
    medicine = med_result.scalar_one_or_none()

    if not medicine:
        return SyncStatusResponse(status="failed", error="Medicine record not found.")

    medicine_dict = {
        "id":            str(medicine.id),
        "name":          medicine.name,
        "category":      medicine.category,
        "batch_number":  medicine.batch_number,
        "expiry_date":   medicine.expiry_date.isoformat() if medicine.expiry_date else None,
        "buying_price":  medicine.buying_price  or 0.0,
        "selling_price": medicine.selling_price or 0.0,
        "quantity":      medicine.quantity      or 0,
        "reorder_level": medicine.reorder_level or 10,
        "description":   medicine.description,
        "is_active":     medicine.is_active,
        "created_at":    medicine.created_at.isoformat() if medicine.created_at else None,
    }

    return SyncStatusResponse(status="completed", medicine=medicine_dict)
