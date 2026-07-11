"""
M-Pesa STK Push router — all Paystack communication lives here.

Endpoints
---------
POST /mpesa/charge          — initiate STK Push, return reference
GET  /mpesa/verify/{ref}    — poll Paystack, update DB, return status
GET  /mpesa/status/{ref}    — read current status from DB only (no Paystack call)

The desktop client calls /mpesa/charge, shows a "waiting" dialog,
then polls /mpesa/verify/{ref} every 4 s until it gets
{"status": "success"} or {"status": "failed"}.
"""
import uuid
import os
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.config import PAYSTACK_SECRET_KEY, log
from backend.database import get_db
from backend.models import MpesaTransaction, User
from backend.auth import require_profile_complete, get_tenant_id

router = APIRouter(prefix="/mpesa", tags=["M-Pesa"])

_CHARGE_URL = "https://api.paystack.co/charge"
_VERIFY_URL = "https://api.paystack.co/transaction/verify/{reference}"

# Cloudflare-friendly headers
_PAYSTACK_HEADERS = {
    "Content-Type":  "application/json",
    "Accept":        "application/json",
    "User-Agent":    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Cache-Control": "no-cache",
}


# ── Schemas ───────────────────────────────────────────────────────────────────

class ChargeRequest(BaseModel):
    email:        str
    amount:       float       # KES — server converts to subunits
    phone_number: str
    description:  Optional[str] = "Pharmacy payment"


class ChargeResponse(BaseModel):
    reference:   str
    status:      str          # "pending" | "send_otp" | etc.
    message:     str


class VerifyResponse(BaseModel):
    reference:   str
    status:      str          # "pending" | "success" | "failed"
    amount:      float
    message:     str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _auth_headers() -> dict:
    key = PAYSTACK_SECRET_KEY
    if not key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PAYSTACK_SECRET_KEY is not configured on the server.",
        )
    return {**_PAYSTACK_HEADERS, "Authorization": f"Bearer {key}"}


def _normalize_phone(raw: str) -> str:
    """
    Normalize to E.164 format WITH the leading + that Paystack requires:
    +254XXXXXXXXX (13 chars total)

    Accepts: 07XXXXXXXX / +2547XXXXXXXX / 2547XXXXXXXX / 7XXXXXXXX
    """
    phone = raw.strip().replace(" ", "").replace("-", "")

    # Strip leading + so we can work with digits only
    if phone.startswith("+"):
        phone = phone[1:]

    # Convert local Kenyan formats → 254XXXXXXXXX
    if phone.startswith("0") and len(phone) == 10:
        phone = "254" + phone[1:]          # 07XXXXXXXX → 2547XXXXXXXX
    elif (phone.startswith("7") or phone.startswith("1")) and len(phone) == 9:
        phone = "254" + phone              # 7XXXXXXXX  → 2547XXXXXXXX

    # Validate: must be 12 digits starting with 254
    if not phone.startswith("254") or len(phone) != 12 or not phone.isdigit():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Invalid phone number: '{raw}'. "
                "Use format 07XXXXXXXX, +254XXXXXXXXX, or 254XXXXXXXXX."
            ),
        )

    # Paystack mobile_money.phone requires E.164 WITH the + prefix
    return "+" + phone


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/charge", response_model=ChargeResponse)
async def initiate_charge(
    req: ChargeRequest,
    user: User = Depends(require_profile_complete),
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger an M-Pesa STK Push via Paystack.

    1. Validates and normalises the phone number.
    2. POSTs to Paystack /charge.
    3. Saves a pending MpesaTransaction row.
    4. Returns the reference the client uses to poll /verify/{ref}.
    """
    phone           = _normalize_phone(req.phone_number)   # returns +254XXXXXXXXX
    admin_id        = get_tenant_id(user)
    amount_subunits = int(round(req.amount * 100))
    reference       = f"PHARM-{uuid.uuid4().hex[:12].upper()}"

    payload = {
        "email":        req.email,
        "amount":       amount_subunits,
        "currency":     "KES",
        "reference":    reference,
        "mobile_money": {
            "phone":    phone,          # E.164 with + e.g. +254742041208
            "provider": "mpesa",
        },
    }

    log.info("Initiating M-Pesa charge: ref=%s phone=%s amount=%s KES",
             reference, phone, req.amount)

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                _CHARGE_URL, json=payload, headers=_auth_headers()
            )
            data = resp.json()
    except httpx.RequestError as exc:
        log.error("Paystack request error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not reach Paystack: {exc}",
        )

    if not data.get("status"):
        msg = data.get("message", "Unknown Paystack error")
        log.warning("Paystack charge failed: %s — payload: %s", msg, data)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"M-Pesa charge failed: {msg}",
        )

    # Persist transaction
    tx = MpesaTransaction(
        admin_id     = admin_id,
        reference    = reference,
        email        = req.email,
        phone_number = phone,
        amount       = req.amount,
        currency     = "KES",
        status       = "pending",
        paystack_data = data.get("data"),
    )
    db.add(tx)
    await db.commit()

    ps_status = (data.get("data") or {}).get("status", "pending")
    return ChargeResponse(
        reference = reference,
        status    = ps_status,
        message   = data.get("message", "STK Push sent"),
    )


@router.get("/verify/{reference}", response_model=VerifyResponse)
async def verify_charge(
    reference: str,
    user: User = Depends(require_profile_complete),
    db: AsyncSession = Depends(get_db),
):
    """
    Poll Paystack for the current transaction status and sync to DB.

    The desktop client calls this every 4 s.
    Returns {"status": "success"} when payment is confirmed,
    {"status": "failed"} on failure, or {"status": "pending"} while waiting.
    """
    # Load local record first
    result = await db.execute(
        select(MpesaTransaction).where(MpesaTransaction.reference == reference)
    )
    tx = result.scalar_one_or_none()
    if not tx:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transaction {reference} not found.",
        )

    # If already resolved, return immediately — no Paystack call needed
    if tx.status in ("success", "failed"):
        return VerifyResponse(
            reference = reference,
            status    = tx.status,
            amount    = tx.amount,
            message   = f"Payment {tx.status}.",
        )

    # Poll Paystack
    url = _VERIFY_URL.format(reference=reference)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url, headers=_auth_headers())
            data = resp.json()
    except httpx.RequestError as exc:
        log.warning("Paystack verify request error: %s", exc)
        # Don't fail — return pending so the client keeps polling
        return VerifyResponse(
            reference = reference,
            status    = "pending",
            amount    = tx.amount,
            message   = "Network error — retrying.",
        )

    ps_data   = data.get("data") or {}
    ps_status = (ps_data.get("status") or "pending").lower()

    # Map Paystack statuses to our simplified set
    if ps_status == "success":
        resolved = "success"
    elif ps_status in ("failed", "abandoned", "reversed"):
        resolved = "failed"
    else:
        resolved = "pending"   # send_otp / ongoing / processing

    # Update DB if status changed
    if resolved != "pending" and tx.status != resolved:
        tx.status       = resolved
        tx.updated_at   = datetime.now(timezone.utc)
        tx.paystack_data = ps_data
        await db.commit()
        log.info("M-Pesa transaction %s → %s", reference, resolved)

    msg_map = {
        "success": "Payment confirmed!",
        "failed":  "Payment failed.",
        "pending": "Waiting for M-Pesa PIN...",
    }
    return VerifyResponse(
        reference = reference,
        status    = resolved,
        amount    = tx.amount,
        message   = msg_map[resolved],
    )


@router.get("/status/{reference}", response_model=VerifyResponse)
async def get_status(
    reference: str,
    user: User = Depends(require_profile_complete),
    db: AsyncSession = Depends(get_db),
):
    """Return the stored status from DB only — no Paystack call."""
    result = await db.execute(
        select(MpesaTransaction).where(MpesaTransaction.reference == reference)
    )
    tx = result.scalar_one_or_none()
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found.")
    return VerifyResponse(
        reference = reference,
        status    = tx.status,
        amount    = tx.amount,
        message   = f"Payment {tx.status}.",
    )
