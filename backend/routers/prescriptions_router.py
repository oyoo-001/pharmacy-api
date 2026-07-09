"""Prescriptions CRUD router."""
import uuid
from typing import Optional, List
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_
from sqlalchemy.orm import selectinload
from pydantic import BaseModel, Field

from backend.database import get_db
from backend.models import Prescription, PrescriptionItem, Medicine, User
from backend.auth import get_current_user, get_tenant_id, require_profile_complete

router = APIRouter(prefix="/prescriptions", tags=["Prescriptions"])


# ---------- Schemas ----------

class PrescriptionItemCreate(BaseModel):
    medicine_id: uuid.UUID
    dosage: Optional[str] = None
    duration: Optional[str] = None
    instructions: Optional[str] = None


class PrescriptionCreate(BaseModel):
    patient_name: str = Field(min_length=1, max_length=200)
    patient_age: Optional[int] = None
    patient_gender: Optional[str] = None
    doctor_name: Optional[str] = None
    doctor_phone: Optional[str] = None
    diagnosis: Optional[str] = None
    notes: Optional[str] = None
    items: List[PrescriptionItemCreate] = []


class PrescriptionUpdate(BaseModel):
    patient_name: Optional[str] = None
    patient_age: Optional[int] = None
    patient_gender: Optional[str] = None
    doctor_name: Optional[str] = None
    doctor_phone: Optional[str] = None
    diagnosis: Optional[str] = None
    notes: Optional[str] = None


class PrescriptionItemResponse(BaseModel):
    id: uuid.UUID
    medicine_id: uuid.UUID
    dosage: Optional[str] = None
    duration: Optional[str] = None
    instructions: Optional[str] = None

    class Config:
        from_attributes = True


class PrescriptionResponse(BaseModel):
    id: uuid.UUID
    admin_id: uuid.UUID
    patient_name: str
    patient_age: Optional[int] = None
    patient_gender: Optional[str] = None
    doctor_name: Optional[str] = None
    doctor_phone: Optional[str] = None
    diagnosis: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    items: List[PrescriptionItemResponse] = []

    class Config:
        from_attributes = True


# ---------- Endpoints ----------

@router.get("", response_model=list[PrescriptionResponse])
async def list_prescriptions(
    search: Optional[str] = Query(None),
    limit: int = Query(100, le=1000),
    offset: int = Query(0),
    user: User = Depends(require_profile_complete),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = get_tenant_id(user)
    filters = [Prescription.admin_id == tenant_id]
    if search:
        term = f"%{search}%"
        filters.append(
            or_(Prescription.patient_name.ilike(term),
                Prescription.doctor_name.ilike(term),
                Prescription.diagnosis.ilike(term))
        )
    result = await db.execute(
        select(Prescription).options(selectinload(Prescription.items))
        .where(and_(*filters))
        .order_by(Prescription.created_at.desc())
        .limit(limit).offset(offset)
    )
    return result.scalars().all()


@router.get("/{prescription_id}", response_model=PrescriptionResponse)
async def get_prescription(
    prescription_id: uuid.UUID,
    user: User = Depends(require_profile_complete),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = get_tenant_id(user)
    result = await db.execute(
        select(Prescription).options(selectinload(Prescription.items))
        .where(and_(Prescription.id == prescription_id, Prescription.admin_id == tenant_id))
    )
    prescription = result.scalar_one_or_none()
    if not prescription:
        raise HTTPException(status_code=404, detail="Prescription not found")
    return prescription


@router.post("", response_model=PrescriptionResponse, status_code=201)
async def create_prescription(
    data: PrescriptionCreate,
    user: User = Depends(require_profile_complete),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = get_tenant_id(user)

    prescription = Prescription(
        admin_id=tenant_id,
        patient_name=data.patient_name,
        patient_age=data.patient_age,
        patient_gender=data.patient_gender,
        doctor_name=data.doctor_name,
        doctor_phone=data.doctor_phone,
        diagnosis=data.diagnosis,
        notes=data.notes,
    )
    db.add(prescription)
    await db.flush()

    for item_data in data.items:
        # Validate medicine belongs to this tenant
        med_result = await db.execute(
            select(Medicine).where(
                and_(Medicine.id == item_data.medicine_id,
                     Medicine.admin_id == tenant_id)
            )
        )
        if not med_result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail=f"Medicine {item_data.medicine_id} not found")

        prx_item = PrescriptionItem(
            admin_id=tenant_id,
            prescription_id=prescription.id,
            medicine_id=item_data.medicine_id,
            dosage=item_data.dosage,
            duration=item_data.duration,
            instructions=item_data.instructions,
        )
        db.add(prx_item)

    await db.commit()

    result = await db.execute(
        select(Prescription).options(selectinload(Prescription.items))
        .where(Prescription.id == prescription.id)
    )
    return result.scalar_one()


@router.put("/{prescription_id}", response_model=PrescriptionResponse)
async def update_prescription(
    prescription_id: uuid.UUID,
    data: PrescriptionUpdate,
    user: User = Depends(require_profile_complete),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = get_tenant_id(user)
    result = await db.execute(
        select(Prescription).options(selectinload(Prescription.items))
        .where(and_(Prescription.id == prescription_id, Prescription.admin_id == tenant_id))
    )
    prescription = result.scalar_one_or_none()
    if not prescription:
        raise HTTPException(status_code=404, detail="Prescription not found")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(prescription, field, value)

    await db.commit()
    await db.refresh(prescription)
    return prescription


@router.delete("/{prescription_id}", status_code=204)
async def delete_prescription(
    prescription_id: uuid.UUID,
    user: User = Depends(require_profile_complete),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = get_tenant_id(user)
    result = await db.execute(
        select(Prescription).where(
            and_(Prescription.id == prescription_id, Prescription.admin_id == tenant_id)
        )
    )
    prescription = result.scalar_one_or_none()
    if not prescription:
        raise HTTPException(status_code=404, detail="Prescription not found")

    await db.delete(prescription)
    await db.commit()
