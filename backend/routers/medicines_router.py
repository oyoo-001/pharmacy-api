"""Medicines CRUD router."""
import uuid
from typing import Optional
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func
from pydantic import BaseModel, Field

from backend.database import get_db
from backend.models import Medicine
from backend.auth import get_current_user, get_tenant_id
from backend.models import User

router = APIRouter(prefix="/medicines", tags=["Medicines"])


# ---------- Schemas ----------

class MedicineCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category: Optional[str] = None
    batch_number: Optional[str] = None
    expiry_date: Optional[date] = None
    buying_price: Optional[float] = None
    selling_price: float
    quantity: int = 0
    reorder_level: int = 10
    description: Optional[str] = None


class MedicineUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    batch_number: Optional[str] = None
    expiry_date: Optional[date] = None
    buying_price: Optional[float] = None
    selling_price: Optional[float] = None
    quantity: Optional[int] = None
    reorder_level: Optional[int] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class MedicineResponse(BaseModel):
    id: uuid.UUID
    admin_id: uuid.UUID
    name: str
    category: Optional[str] = None
    batch_number: Optional[str] = None
    expiry_date: Optional[date] = None
    buying_price: Optional[float] = None
    selling_price: Optional[float] = None
    quantity: int
    reorder_level: int
    description: Optional[str] = None
    is_active: bool

    class Config:
        from_attributes = True


# ---------- Endpoints ----------

@router.get("", response_model=list[MedicineResponse])
async def list_medicines(
    search: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    in_stock_only: bool = Query(False),
    low_stock_only: bool = Query(False),
    limit: int = Query(1000, le=5000),
    offset: int = Query(0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = get_tenant_id(user)
    filters = [Medicine.admin_id == tenant_id, Medicine.is_active == True]

    if search:
        term = f"%{search}%"
        filters.append(
            or_(Medicine.name.ilike(term), Medicine.category.ilike(term),
                Medicine.batch_number.ilike(term))
        )
    if category:
        filters.append(Medicine.category == category)
    if in_stock_only:
        filters.append(Medicine.quantity > 0)
    if low_stock_only:
        filters.append(Medicine.quantity <= Medicine.reorder_level)

    result = await db.execute(
        select(Medicine).where(and_(*filters))
        .order_by(Medicine.name).limit(limit).offset(offset)
    )
    return result.scalars().all()


@router.get("/categories")
async def list_categories(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = get_tenant_id(user)
    result = await db.execute(
        select(Medicine.category).where(
            and_(Medicine.admin_id == tenant_id, Medicine.category.isnot(None),
                 Medicine.category != "", Medicine.is_active == True)
        ).distinct().order_by(Medicine.category)
    )
    return [r[0] for r in result.all() if r[0]]


@router.get("/{medicine_id}", response_model=MedicineResponse)
async def get_medicine(
    medicine_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = get_tenant_id(user)
    result = await db.execute(
        select(Medicine).where(
            and_(Medicine.id == medicine_id, Medicine.admin_id == tenant_id)
        )
    )
    medicine = result.scalar_one_or_none()
    if not medicine:
        raise HTTPException(status_code=404, detail="Medicine not found")
    return medicine


@router.post("", response_model=MedicineResponse, status_code=201)
async def create_medicine(
    data: MedicineCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = get_tenant_id(user)

    # Check duplicate batch number within tenant
    if data.batch_number:
        existing = await db.execute(
            select(Medicine).where(
                and_(Medicine.admin_id == tenant_id,
                     Medicine.batch_number == data.batch_number)
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail=f"Batch number '{data.batch_number}' already exists")

    medicine = Medicine(admin_id=tenant_id, **data.model_dump())
    db.add(medicine)
    await db.commit()
    await db.refresh(medicine)
    return medicine


@router.put("/{medicine_id}", response_model=MedicineResponse)
async def update_medicine(
    medicine_id: uuid.UUID,
    data: MedicineUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = get_tenant_id(user)
    result = await db.execute(
        select(Medicine).where(
            and_(Medicine.id == medicine_id, Medicine.admin_id == tenant_id)
        )
    )
    medicine = result.scalar_one_or_none()
    if not medicine:
        raise HTTPException(status_code=404, detail="Medicine not found")

    # Check batch number uniqueness if changing
    if data.batch_number and data.batch_number != medicine.batch_number:
        dup = await db.execute(
            select(Medicine).where(
                and_(Medicine.admin_id == tenant_id,
                     Medicine.batch_number == data.batch_number,
                     Medicine.id != medicine_id)
            )
        )
        if dup.scalar_one_or_none():
            raise HTTPException(status_code=400, detail=f"Batch number '{data.batch_number}' already exists")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(medicine, field, value)

    await db.commit()
    await db.refresh(medicine)
    return medicine


@router.delete("/{medicine_id}", status_code=204)
async def delete_medicine(
    medicine_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = get_tenant_id(user)
    result = await db.execute(
        select(Medicine).where(
            and_(Medicine.id == medicine_id, Medicine.admin_id == tenant_id)
        )
    )
    medicine = result.scalar_one_or_none()
    if not medicine:
        raise HTTPException(status_code=404, detail="Medicine not found")

    # Soft delete
    medicine.is_active = False
    await db.commit()
