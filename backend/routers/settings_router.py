"""Pharmacy settings router."""
import uuid
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from pydantic import BaseModel

from backend.database import get_db
from backend.models import PharmacySetting, User
from backend.auth import get_current_user, require_admin, get_tenant_id

router = APIRouter(prefix="/settings", tags=["Settings"])


# ---------- Schemas ----------

class SettingsUpdate(BaseModel):
    pharmacy_name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    tax_rate: Optional[float] = None
    receipt_footer: Optional[str] = None
    logo_path: Optional[str] = None
    currency_symbol: Optional[str] = None


class SettingsResponse(BaseModel):
    id: uuid.UUID
    admin_id: uuid.UUID
    pharmacy_name: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    tax_rate: Optional[float] = None
    receipt_footer: Optional[str] = None
    logo_path: Optional[str] = None
    currency_symbol: Optional[str] = None

    class Config:
        from_attributes = True


# ---------- Endpoints ----------

@router.get("", response_model=SettingsResponse)
async def get_settings(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = get_tenant_id(user)
    result = await db.execute(
        select(PharmacySetting).where(PharmacySetting.admin_id == tenant_id)
    )
    settings = result.scalar_one_or_none()
    if not settings:
        # Auto-create default settings for this admin
        settings = PharmacySetting(
            admin_id=tenant_id,
            pharmacy_name="Kevin Odongo Pharmacy",
            currency_symbol="KES",
            tax_rate=0.16,
        )
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings


@router.put("", response_model=SettingsResponse)
async def update_settings(
    data: SettingsUpdate,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = get_tenant_id(user)
    result = await db.execute(
        select(PharmacySetting).where(PharmacySetting.admin_id == tenant_id)
    )
    settings = result.scalar_one_or_none()
    if not settings:
        settings = PharmacySetting(admin_id=tenant_id)
        db.add(settings)

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(settings, field, value)

    await db.commit()
    await db.refresh(settings)
    return settings
