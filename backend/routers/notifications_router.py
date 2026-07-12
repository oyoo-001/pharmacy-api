"""Notifications router — CRUD + helper for creating notifications."""
import uuid
from typing import Optional
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update, delete
from pydantic import BaseModel

from backend.database import get_db
from backend.models import Notification, PharmacySetting, User
from backend.auth import get_current_user, get_tenant_id

router = APIRouter(prefix="/notifications", tags=["Notifications"])


# ---------- Schemas ----------

class NotificationCreate(BaseModel):
    title: str
    message: str
    type: str = "info"       # info | medicine | low_stock | message | alert
    link: Optional[str] = None


class NotificationResponse(BaseModel):
    id: uuid.UUID
    title: str
    message: str
    type: str
    is_read: bool
    link: Optional[str] = None
    created_at: Optional[str] = None

    class Config:
        from_attributes = True


# ---------- Endpoints ----------

@router.get("/count")
async def notification_count(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return unread notification count for the current admin."""
    tenant_id = get_tenant_id(user)
    result = await db.execute(
        select(func.count()).select_from(Notification).where(
            Notification.admin_id == tenant_id,
            Notification.is_read == False,
        )
    )
    return {"count": result.scalar() or 0}


@router.get("", response_model=list[NotificationResponse])
async def list_notifications(
    unread_only: bool = Query(False),
    limit: int = Query(50, le=200),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return recent notifications for the current admin."""
    tenant_id = get_tenant_id(user)
    filters = [Notification.admin_id == tenant_id]
    if unread_only:
        filters.append(Notification.is_read == False)

    result = await db.execute(
        select(Notification)
        .where(*filters)
        .order_by(Notification.created_at.desc())
        .limit(limit)
    )
    items = result.scalars().all()
    return [
        NotificationResponse(
            id=str(n.id),
            title=n.title,
            message=n.message,
            type=n.type,
            is_read=n.is_read,
            link=n.link,
            created_at=n.created_at.isoformat() if n.created_at else "",
        )
        for n in items
    ]


@router.put("/{notification_id}/read")
async def mark_read(
    notification_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark a single notification as read."""
    tenant_id = get_tenant_id(user)
    result = await db.execute(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.admin_id == tenant_id,
        )
    )
    n = result.scalar_one_or_none()
    if not n:
        raise HTTPException(status_code=404, detail="Notification not found.")
    n.is_read = True
    await db.commit()
    return {"ok": True}


@router.put("/read-all")
async def mark_all_read(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark all notifications as read for the current admin."""
    tenant_id = get_tenant_id(user)
    await db.execute(
        update(Notification)
        .where(Notification.admin_id == tenant_id, Notification.is_read == False)
        .values(is_read=True)
    )
    await db.commit()
    return {"ok": True}


@router.delete("/{notification_id}")
async def delete_notification(
    notification_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a single notification."""
    tenant_id = get_tenant_id(user)
    result = await db.execute(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.admin_id == tenant_id,
        )
    )
    n = result.scalar_one_or_none()
    if not n:
        raise HTTPException(status_code=404, detail="Notification not found.")
    await db.delete(n)
    await db.commit()
    return {"ok": True}


@router.delete("")
async def clear_all(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete ALL notifications for the current admin."""
    tenant_id = get_tenant_id(user)
    await db.execute(
        delete(Notification).where(Notification.admin_id == tenant_id)
    )
    await db.commit()
    return {"ok": True}


# ── Helper function for other routers to create notifications ─────────────

async def create_notification(
    db: AsyncSession,
    admin_id: uuid.UUID,
    title: str,
    message: str,
    ntype: str = "info",
    link: Optional[str] = None,
) -> Optional[Notification]:
    """
    Create a notification if notifications are enabled for this admin.
    Returns the Notification object, or None if disabled.
    """
    # Check if notifications are enabled
    result = await db.execute(
        select(PharmacySetting).where(PharmacySetting.admin_id == admin_id)
    )
    settings = result.scalar_one_or_none()
    if settings and settings.notifications_enabled is False:
        return None

    notif = Notification(
        admin_id=admin_id,
        title=title,
        message=message,
        type=ntype,
        link=link,
    )
    db.add(notif)
    return notif
