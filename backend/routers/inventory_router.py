"""Inventory router — stock levels and transaction history."""
import uuid
from typing import Optional
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_
from pydantic import BaseModel

from backend.database import get_db
from backend.models import Medicine, InventoryTransaction, User, TransactionType
from backend.auth import get_current_user, get_tenant_id, require_profile_complete

router = APIRouter(prefix="/inventory", tags=["Inventory"])


class TransactionResponse(BaseModel):
    id: uuid.UUID
    medicine_id: uuid.UUID
    transaction_type: str
    quantity: int
    unit_price: Optional[float] = None
    total_price: Optional[float] = None
    notes: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class StockSummary(BaseModel):
    total_medicines: int
    total_units: int
    low_stock: int
    out_of_stock: int


@router.get("/summary", response_model=StockSummary)
async def stock_summary(
    user: User = Depends(require_profile_complete),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = get_tenant_id(user)
    result = await db.execute(
        select(Medicine).where(
            and_(Medicine.admin_id == tenant_id, Medicine.is_active == True)
        )
    )
    medicines = result.scalars().all()
    total_units = sum(m.quantity for m in medicines)
    low_stock = sum(1 for m in medicines if 0 < m.quantity <= m.reorder_level)
    out_of_stock = sum(1 for m in medicines if m.quantity <= 0)
    return StockSummary(
        total_medicines=len(medicines),
        total_units=total_units,
        low_stock=low_stock,
        out_of_stock=out_of_stock,
    )


@router.get("/transactions", response_model=list[TransactionResponse])
async def list_transactions(
    transaction_type: Optional[str] = Query(None),
    medicine_id: Optional[uuid.UUID] = Query(None),
    limit: int = Query(100, le=1000),
    offset: int = Query(0),
    user: User = Depends(require_profile_complete),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = get_tenant_id(user)
    filters = [InventoryTransaction.admin_id == tenant_id]
    if transaction_type and transaction_type.lower() != "all":
        filters.append(InventoryTransaction.transaction_type == transaction_type.lower())
    if medicine_id:
        filters.append(InventoryTransaction.medicine_id == medicine_id)

    result = await db.execute(
        select(InventoryTransaction)
        .where(and_(*filters))
        .order_by(InventoryTransaction.created_at.desc())
        .limit(limit).offset(offset)
    )
    return result.scalars().all()


@router.get("/alerts/low-stock")
async def low_stock_alerts(
    user: User = Depends(require_profile_complete),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = get_tenant_id(user)
    result = await db.execute(
        select(Medicine).where(
            and_(Medicine.admin_id == tenant_id,
                 Medicine.quantity <= Medicine.reorder_level,
                 Medicine.is_active == True)
        ).order_by(Medicine.quantity)
    )
    medicines = result.scalars().all()
    return [
        {
            "id": str(m.id),
            "name": m.name,
            "quantity": m.quantity,
            "reorder_level": m.reorder_level,
            "status": "Out of Stock" if m.quantity <= 0 else "Low Stock",
        }
        for m in medicines
    ]


@router.get("/alerts/expiring")
async def expiring_alerts(
    days: int = Query(30, ge=1, le=365),
    user: User = Depends(require_profile_complete),
    db: AsyncSession = Depends(get_db),
):
    from datetime import timedelta, date
    tenant_id = get_tenant_id(user)
    today = datetime.now(timezone.utc).date()
    cutoff = today + timedelta(days=days)

    result = await db.execute(
        select(Medicine).where(
            and_(Medicine.admin_id == tenant_id,
                 Medicine.expiry_date.isnot(None),
                 Medicine.expiry_date >= today,
                 Medicine.expiry_date <= cutoff,
                 Medicine.is_active == True)
        ).order_by(Medicine.expiry_date)
    )
    medicines = result.scalars().all()
    return [
        {
            "id": str(m.id),
            "name": m.name,
            "batch_number": m.batch_number,
            "expiry_date": str(m.expiry_date),
            "days_left": (m.expiry_date - today).days,
        }
        for m in medicines
    ]
