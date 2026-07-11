"""
Payment Settings router — admin-only CRUD for per-tenant Paystack credentials.

GET  /payment-settings        — read own Paystack keys (secret masked)
PUT  /payment-settings        — save / update keys
POST /payment-settings/test   — verify keys by hitting Paystack /bank endpoint
"""
import uuid
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.database import get_db
from backend.models import PaymentSettings, User
from backend.auth import require_admin, get_tenant_id

router = APIRouter(prefix="/payment-settings", tags=["Payment Settings"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class PaymentSettingsUpdate(BaseModel):
    paystack_secret_key: Optional[str] = None
    paystack_public_key: Optional[str] = None
    is_live:             Optional[bool] = None


class PaymentSettingsResponse(BaseModel):
    id:                  uuid.UUID
    admin_id:            uuid.UUID
    paystack_public_key: Optional[str] = None
    # Secret key is masked — only show last 6 chars so admin can confirm which key is set
    paystack_secret_key_hint: Optional[str] = None
    is_live:             bool = True

    class Config:
        from_attributes = True


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mask_key(key: Optional[str]) -> Optional[str]:
    """Return last 6 chars of the key as a hint, e.g. 'sk_live_...cc37'."""
    if not key:
        return None
    return f"...{key[-6:]}"


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=PaymentSettingsResponse)
async def get_payment_settings(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Return current payment settings for this admin (secret is masked)."""
    admin_id = get_tenant_id(user)
    result = await db.execute(
        select(PaymentSettings).where(PaymentSettings.admin_id == admin_id)
    )
    ps = result.scalar_one_or_none()

    if not ps:
        # Return empty config — admin hasn't set keys yet
        return PaymentSettingsResponse(
            id=uuid.uuid4(),
            admin_id=admin_id,
            paystack_public_key=None,
            paystack_secret_key_hint=None,
            is_live=True,
        )

    return PaymentSettingsResponse(
        id=ps.id,
        admin_id=ps.admin_id,
        paystack_public_key=ps.paystack_public_key,
        paystack_secret_key_hint=_mask_key(ps.paystack_secret_key),
        is_live=ps.is_live if ps.is_live is not None else True,
    )


@router.put("", response_model=PaymentSettingsResponse)
async def update_payment_settings(
    data: PaymentSettingsUpdate,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create or update Paystack credentials for this admin."""
    admin_id = get_tenant_id(user)

    result = await db.execute(
        select(PaymentSettings).where(PaymentSettings.admin_id == admin_id)
    )
    ps = result.scalar_one_or_none()

    if ps is None:
        ps = PaymentSettings(admin_id=admin_id)
        db.add(ps)

    if data.paystack_secret_key is not None:
        ps.paystack_secret_key = data.paystack_secret_key.strip() or None
    if data.paystack_public_key is not None:
        ps.paystack_public_key = data.paystack_public_key.strip() or None
    if data.is_live is not None:
        ps.is_live = data.is_live

    await db.commit()
    await db.refresh(ps)

    return PaymentSettingsResponse(
        id=ps.id,
        admin_id=ps.admin_id,
        paystack_public_key=ps.paystack_public_key,
        paystack_secret_key_hint=_mask_key(ps.paystack_secret_key),
        is_live=ps.is_live,
    )


@router.post("/test")
async def test_paystack_keys(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Verify saved keys by hitting Paystack's /bank endpoint (lightweight, read-only).
    Returns {ok: true, message: "..."} or raises 400.
    """
    admin_id = get_tenant_id(user)
    result = await db.execute(
        select(PaymentSettings).where(PaymentSettings.admin_id == admin_id)
    )
    ps = result.scalar_one_or_none()

    if not ps or not ps.paystack_secret_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No Paystack secret key configured. Save your keys first.",
        )

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://api.paystack.co/bank?country=kenya&currency=KES&perPage=1",
                headers={
                    "Authorization": f"Bearer {ps.paystack_secret_key}",
                    "Accept": "application/json",
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
                    ),
                },
            )
            data = resp.json()
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not reach Paystack: {exc}",
        )

    if data.get("status"):
        mode = "Live" if ps.is_live else "Test"
        return {"ok": True, "message": f"Keys are valid ({mode} mode)."}

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Paystack rejected the key: {data.get('message', 'Unknown error')}",
    )
