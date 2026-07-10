"""
Update management router.
Admin-only CRUD for app releases + public metadata.json endpoint.
"""
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from backend.database import get_db
from backend.models import User, AppUpdate
from backend.schemas import AppUpdateCreate, AppUpdateResponse, UpdateMetadataResponse
from backend.auth import require_admin

router = APIRouter(tags=["Updates"])


# ---------------------------------------------------------------------------
# Public: latest update metadata (no auth)
# ---------------------------------------------------------------------------

@router.get("/updates/metadata.json", response_model=UpdateMetadataResponse)
async def latest_update_metadata(db: AsyncSession = Depends(get_db)):
    """Served to clients on startup to check for new versions."""
    result = await db.execute(
        select(AppUpdate)
        .where(AppUpdate.is_active == True)  # noqa: E712
        .order_by(desc(AppUpdate.release_date))
        .limit(1)
    )
    release = result.scalar_one_or_none()
    if release is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No updates available",
        )
    return UpdateMetadataResponse(
        version=release.version,
        bundle_url=release.bundle_url,
        release_date=release.release_date,
        release_notes=release.release_notes,
        min_app_version=release.min_app_version,
    )


# ---------------------------------------------------------------------------
# Admin: list all releases
# ---------------------------------------------------------------------------

@router.get("/api/updates", response_model=list[AppUpdateResponse])
async def list_updates(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(
        select(AppUpdate).order_by(desc(AppUpdate.release_date))
    )
    return result.scalars().all()


# ---------------------------------------------------------------------------
# Admin: create a new release
# ---------------------------------------------------------------------------

@router.post("/api/updates", response_model=AppUpdateResponse, status_code=status.HTTP_201_CREATED)
async def create_update(
    req: AppUpdateCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    # Deactivate any existing active release with the same version
    dup = await db.execute(
        select(AppUpdate).where(AppUpdate.version == req.version)
    )
    existing = dup.scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Version {req.version} already exists",
        )

    release = AppUpdate(
        version=req.version,
        bundle_url=req.bundle_url,
        release_notes=req.release_notes,
        min_app_version=req.min_app_version,
        created_by=admin.id,
    )
    db.add(release)
    await db.commit()
    await db.refresh(release)
    return release


# ---------------------------------------------------------------------------
# Admin: toggle active / deactivate
# ---------------------------------------------------------------------------

@router.patch("/api/updates/{update_id}/toggle")
async def toggle_update_active(
    update_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(
        select(AppUpdate).where(AppUpdate.id == update_id)
    )
    release = result.scalar_one_or_none()
    if release is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    release.is_active = not release.is_active
    await db.commit()
    return {"id": str(release.id), "is_active": release.is_active}


# ---------------------------------------------------------------------------
# Admin: delete a release
# ---------------------------------------------------------------------------

@router.delete("/api/updates/{update_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_update(
    update_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
):
    result = await db.execute(
        select(AppUpdate).where(AppUpdate.id == update_id)
    )
    release = result.scalar_one_or_none()
    if release is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await db.delete(release)
    await db.commit()
