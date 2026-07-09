import uuid
from datetime import date, datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from backend.database import get_db, engine
from backend.auth import get_current_user, require_admin, get_tenant_id
from backend.models import User

router = APIRouter(prefix="/sync", tags=["Sync"])


@router.get("/db-health")
async def db_health():
    """Check if the PostgreSQL database is reachable."""
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            row = result.scalar()
            return {"status": "connected", "db": "postgresql"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database unreachable: {str(e)}")


@router.post("/medicines")
async def sync_medicines(
    records: list[dict],
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = get_tenant_id(user)
    synced = 0
    for rec in records:
        rec["admin_id"] = tenant_id
        rec["id"] = rec.get("id", uuid.uuid4())
        if isinstance(rec.get("expiry_date"), str):
            rec["expiry_date"] = datetime.strptime(rec["expiry_date"], "%Y-%m-%d").date()
        stmt = text("""
            INSERT INTO medicines (id, admin_id, name, category, batch_number, expiry_date,
                buying_price, selling_price, quantity, reorder_level, description, is_active,
                created_at, updated_at)
            VALUES (:id, :admin_id, :name, :category, :batch_number, :expiry_date,
                :buying_price, :selling_price, :quantity, :reorder_level, :description,
                COALESCE(:is_active, true), COALESCE(:created_at, NOW()), COALESCE(:updated_at, NOW()))
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name, category = EXCLUDED.category,
                batch_number = EXCLUDED.batch_number, expiry_date = EXCLUDED.expiry_date,
                buying_price = EXCLUDED.buying_price, selling_price = EXCLUDED.selling_price,
                quantity = EXCLUDED.quantity, reorder_level = EXCLUDED.reorder_level,
                description = EXCLUDED.description, is_active = EXCLUDED.is_active,
                updated_at = NOW()
        """)
        await db.execute(stmt, rec)
        synced += 1
    await db.commit()
    return {"synced": synced, "entity": "medicines"}


@router.post("/suppliers")
async def sync_suppliers(
    records: list[dict],
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = get_tenant_id(user)
    synced = 0
    for rec in records:
        rec["admin_id"] = tenant_id
        rec["id"] = rec.get("id", uuid.uuid4())
        stmt = text("""
            INSERT INTO suppliers (id, admin_id, name, contact_person, phone, email,
                address, tax_id, payment_terms, is_active, created_at, updated_at)
            VALUES (:id, :admin_id, :name, :contact_person, :phone, :email,
                :address, :tax_id, :payment_terms, COALESCE(:is_active, true),
                COALESCE(:created_at, NOW()), COALESCE(:updated_at, NOW()))
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name, contact_person = EXCLUDED.contact_person,
                phone = EXCLUDED.phone, email = EXCLUDED.email,
                address = EXCLUDED.address, tax_id = EXCLUDED.tax_id,
                payment_terms = EXCLUDED.payment_terms, is_active = EXCLUDED.is_active,
                updated_at = NOW()
        """)
        await db.execute(stmt, rec)
        synced += 1
    await db.commit()
    return {"synced": synced, "entity": "suppliers"}


@router.post("/sales")
async def sync_sales(
    records: list[dict],
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = get_tenant_id(user)
    synced = 0
    for rec in records:
        rec["admin_id"] = tenant_id
        rec["id"] = rec.get("id", uuid.uuid4())
        items = rec.pop("items", [])
        stmt = text("""
            INSERT INTO sales (id, admin_id, invoice_number, user_id, customer_name,
                customer_phone, subtotal, tax, discount, total, payment_method,
                payment_status, created_at)
            VALUES (:id, :admin_id, :invoice_number, :user_id, :customer_name,
                :customer_phone, :subtotal, :tax, :discount, :total, :payment_method,
                :payment_status, COALESCE(:created_at, NOW()))
            ON CONFLICT (id) DO UPDATE SET
                customer_name = EXCLUDED.customer_name, total = EXCLUDED.total,
                payment_status = EXCLUDED.payment_status
        """)
        await db.execute(stmt, rec)
        for item in items:
            item["admin_id"] = tenant_id
            item["sale_id"] = rec["id"]
            item["id"] = item.get("id", uuid.uuid4())
            istmt = text("""
                INSERT INTO sale_items (id, admin_id, sale_id, medicine_id, quantity,
                    unit_price, total_price, created_at)
                VALUES (:id, :admin_id, :sale_id, :medicine_id, :quantity,
                    :unit_price, :total_price, COALESCE(:created_at, NOW()))
                ON CONFLICT (id) DO NOTHING
            """)
            await db.execute(istmt, item)
        synced += 1
    await db.commit()
    return {"synced": synced, "entity": "sales"}


@router.post("/prescriptions")
async def sync_prescriptions(
    records: list[dict],
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = get_tenant_id(user)
    synced = 0
    for rec in records:
        rec["admin_id"] = tenant_id
        rec["id"] = rec.get("id", uuid.uuid4())
        items = rec.pop("items", [])
        stmt = text("""
            INSERT INTO prescriptions (id, admin_id, patient_name, patient_age,
                patient_gender, doctor_name, doctor_phone, diagnosis, notes, created_at, updated_at)
            VALUES (:id, :admin_id, :patient_name, :patient_age, :patient_gender,
                :doctor_name, :doctor_phone, :diagnosis, :notes,
                COALESCE(:created_at, NOW()), COALESCE(:updated_at, NOW()))
            ON CONFLICT (id) DO UPDATE SET
                patient_name = EXCLUDED.patient_name, diagnosis = EXCLUDED.diagnosis,
                updated_at = NOW()
        """)
        await db.execute(stmt, rec)
        for item in items:
            item["admin_id"] = tenant_id
            item["prescription_id"] = rec["id"]
            item["id"] = item.get("id", uuid.uuid4())
            istmt = text("""
                INSERT INTO prescription_items (id, admin_id, prescription_id, medicine_id,
                    dosage, duration, instructions, created_at)
                VALUES (:id, :admin_id, :prescription_id, :medicine_id,
                    :dosage, :duration, :instructions, COALESCE(:created_at, NOW()))
                ON CONFLICT (id) DO NOTHING
            """)
            await db.execute(istmt, item)
        synced += 1
    await db.commit()
    return {"synced": synced, "entity": "prescriptions"}
