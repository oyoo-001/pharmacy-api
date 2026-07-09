"""Sales CRUD router."""
import uuid
from typing import Optional, List
from datetime import date, datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func
from sqlalchemy.orm import selectinload
from pydantic import BaseModel, Field

from backend.database import get_db
from backend.models import Sale, SaleItem, Medicine, InventoryTransaction, TransactionType, User
from backend.auth import get_current_user, get_tenant_id, require_profile_complete

router = APIRouter(prefix="/sales", tags=["Sales"])


# ---------- Schemas ----------

class SaleItemCreate(BaseModel):
    medicine_id: uuid.UUID
    quantity: int = Field(ge=1)
    unit_price: float


class SaleCreate(BaseModel):
    customer_name: Optional[str] = "Walk-in Customer"
    customer_phone: Optional[str] = None
    payment_method: str = "cash"
    discount: float = 0.0          # percentage 0-100
    tax_rate: float = 0.16
    items: List[SaleItemCreate]


class SaleItemResponse(BaseModel):
    id: uuid.UUID
    medicine_id: uuid.UUID
    quantity: int
    unit_price: float
    total_price: float

    class Config:
        from_attributes = True


class SaleResponse(BaseModel):
    id: uuid.UUID
    admin_id: uuid.UUID
    invoice_number: str
    user_id: Optional[uuid.UUID] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    subtotal: Optional[float] = None
    tax: Optional[float] = None
    discount: Optional[float] = None
    total: Optional[float] = None
    payment_method: Optional[str] = None
    payment_status: Optional[str] = None
    created_at: datetime
    items: List[SaleItemResponse] = []

    class Config:
        from_attributes = True


# ---------- Helpers ----------

def _generate_invoice(admin_id: uuid.UUID) -> str:
    now = datetime.now(timezone.utc)
    suffix = str(admin_id)[:4].upper()
    return f"INV-{now.strftime('%Y%m%d')}-{now.strftime('%H%M%S')}-{suffix}"


# ---------- Endpoints ----------

@router.get("", response_model=list[SaleResponse])
async def list_sales(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    limit: int = Query(100, le=1000),
    offset: int = Query(0),
    user: User = Depends(require_profile_complete),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = get_tenant_id(user)
    filters = [Sale.admin_id == tenant_id]
    if date_from:
        filters.append(func.date(Sale.created_at) >= date_from)
    if date_to:
        filters.append(func.date(Sale.created_at) <= date_to)

    result = await db.execute(
        select(Sale).options(selectinload(Sale.items))
        .where(and_(*filters))
        .order_by(Sale.created_at.desc())
        .limit(limit).offset(offset)
    )
    return result.scalars().all()


@router.get("/{sale_id}", response_model=SaleResponse)
async def get_sale(
    sale_id: uuid.UUID,
    user: User = Depends(require_profile_complete),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = get_tenant_id(user)
    result = await db.execute(
        select(Sale).options(selectinload(Sale.items))
        .where(and_(Sale.id == sale_id, Sale.admin_id == tenant_id))
    )
    sale = result.scalar_one_or_none()
    if not sale:
        raise HTTPException(status_code=404, detail="Sale not found")
    return sale


@router.post("", response_model=SaleResponse, status_code=201)
async def create_sale(
    data: SaleCreate,
    user: User = Depends(require_profile_complete),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = get_tenant_id(user)

    # Validate stock
    for item_data in data.items:
        result = await db.execute(
            select(Medicine).where(
                and_(Medicine.id == item_data.medicine_id,
                     Medicine.admin_id == tenant_id,
                     Medicine.is_active == True)
            )
        )
        medicine = result.scalar_one_or_none()
        if not medicine:
            raise HTTPException(status_code=404, detail=f"Medicine {item_data.medicine_id} not found")
        if medicine.quantity < item_data.quantity:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient stock for '{medicine.name}': {medicine.quantity} available, {item_data.quantity} requested"
            )

    # Compute totals
    subtotal = sum(i.quantity * i.unit_price for i in data.items)
    tax_amount = subtotal * data.tax_rate
    discount_amount = subtotal * (data.discount / 100)
    total = subtotal + tax_amount - discount_amount

    sale = Sale(
        admin_id=tenant_id,
        invoice_number=_generate_invoice(tenant_id),
        user_id=user.id,
        customer_name=data.customer_name or "Walk-in Customer",
        customer_phone=data.customer_phone,
        subtotal=subtotal,
        tax=tax_amount,
        discount=discount_amount,
        total=total,
        payment_method=data.payment_method,
        payment_status="completed",
    )
    db.add(sale)
    await db.flush()  # get sale.id

    for item_data in data.items:
        sale_item = SaleItem(
            admin_id=tenant_id,
            sale_id=sale.id,
            medicine_id=item_data.medicine_id,
            quantity=item_data.quantity,
            unit_price=item_data.unit_price,
            total_price=item_data.quantity * item_data.unit_price,
        )
        db.add(sale_item)

        # Deduct stock
        result = await db.execute(
            select(Medicine).where(Medicine.id == item_data.medicine_id)
        )
        medicine = result.scalar_one()
        medicine.quantity -= item_data.quantity

        # Record inventory transaction
        tx = InventoryTransaction(
            admin_id=tenant_id,
            medicine_id=item_data.medicine_id,
            transaction_type=TransactionType.sale,
            quantity=-item_data.quantity,
            unit_price=item_data.unit_price,
            total_price=item_data.quantity * item_data.unit_price,
            reference_id=sale.id,
            reference_type="sale",
        )
        db.add(tx)

    await db.commit()

    # Re-fetch with items loaded
    result = await db.execute(
        select(Sale).options(selectinload(Sale.items)).where(Sale.id == sale.id)
    )
    return result.scalar_one()


@router.get("/reports/daily")
async def daily_report(
    report_date: Optional[date] = Query(None),
    user: User = Depends(require_profile_complete),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = get_tenant_id(user)
    target = report_date or datetime.now(timezone.utc).date()

    result = await db.execute(
        select(
            func.count(Sale.id).label("count"),
            func.coalesce(func.sum(Sale.total), 0).label("total"),
            func.coalesce(func.sum(Sale.subtotal), 0).label("subtotal"),
            func.coalesce(func.sum(Sale.tax), 0).label("tax"),
        ).where(
            and_(Sale.admin_id == tenant_id, func.date(Sale.created_at) == target)
        )
    )
    row = result.one()
    return {
        "date": str(target),
        "count": row.count,
        "total": float(row.total),
        "subtotal": float(row.subtotal),
        "tax": float(row.tax),
    }


@router.get("/reports/monthly")
async def monthly_report(
    year: int = Query(None),
    month: int = Query(None),
    user: User = Depends(require_profile_complete),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = get_tenant_id(user)
    now = datetime.now(timezone.utc)
    target_year = year or now.year
    target_month = month or now.month

    result = await db.execute(
        select(Sale).where(
            and_(
                Sale.admin_id == tenant_id,
                func.extract("year", Sale.created_at) == target_year,
                func.extract("month", Sale.created_at) == target_month,
            )
        ).order_by(Sale.created_at.desc())
    )
    sales = result.scalars().all()

    total = sum(s.total or 0 for s in sales)
    return {
        "year": target_year,
        "month": target_month,
        "count": len(sales),
        "total": float(total),
    }
