from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from backend.database import get_db
from backend.models import User, Medicine, Sale, Supplier, Prescription, UserRole
from backend.auth import get_current_user, get_tenant_id, require_profile_complete
from backend.schemas import DashboardStats

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/stats", response_model=DashboardStats)
async def get_dashboard_stats(
    user: User = Depends(require_profile_complete),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = get_tenant_id(user)
    now = datetime.now(timezone.utc)
    first_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    meds = await db.execute(
        select(func.count()).select_from(Medicine).where(Medicine.admin_id == tenant_id)
    )
    total_medicines = meds.scalar()

    low = await db.execute(
        select(func.count()).select_from(Medicine).where(
            and_(Medicine.admin_id == tenant_id, Medicine.quantity <= Medicine.reorder_level)
        )
    )
    low_stock_count = low.scalar()

    expired = await db.execute(
        select(func.count()).select_from(Medicine).where(
            and_(Medicine.admin_id == tenant_id, Medicine.expiry_date < now.date())
        )
    )
    expired_medicines = expired.scalar()

    today_sales = await db.execute(
        select(func.coalesce(func.sum(Sale.total), 0)).where(
            and_(
                Sale.admin_id == tenant_id,
                func.date(Sale.created_at) == now.date(),
            )
        )
    )
    total_sales_today = float(today_sales.scalar() or 0)

    month_sales = await db.execute(
        select(func.coalesce(func.sum(Sale.total), 0)).where(
            and_(
                Sale.admin_id == tenant_id,
                Sale.created_at >= first_of_month,
            )
        )
    )
    total_sales_month = float(month_sales.scalar() or 0)

    supps = await db.execute(
        select(func.count()).select_from(Supplier).where(
            and_(Supplier.admin_id == tenant_id, Supplier.is_active == True)
        )
    )
    active_suppliers = supps.scalar()

    prescs = await db.execute(
        select(func.count()).select_from(Prescription).where(
            Prescription.admin_id == tenant_id
        )
    )
    total_prescriptions = prescs.scalar()

    workers = await db.execute(
        select(func.count()).select_from(User).where(User.admin_id == tenant_id)
    )
    total_workers = workers.scalar()

    return DashboardStats(
        total_medicines=total_medicines,
        low_stock_count=low_stock_count,
        total_sales_today=total_sales_today,
        total_sales_month=total_sales_month,
        active_suppliers=active_suppliers,
        total_prescriptions=total_prescriptions,
        total_workers=total_workers,
        expired_medicines=expired_medicines,
    )
