"""Public feedback submission router."""
import uuid
from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete
from pydantic import BaseModel

from backend.database import get_db
from backend.models import Feedback

router = APIRouter(tags=["Feedback"])


class FeedbackCreate(BaseModel):
    name: str
    email: Optional[str] = None
    message: str


class FeedbackResponse(BaseModel):
    id: uuid.UUID
    name: str
    email: Optional[str] = None
    message: str
    created_at: datetime

    class Config:
        from_attributes = True


@router.post("/feedback", response_model=FeedbackResponse)
async def create_feedback(
    data: FeedbackCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    feedback = Feedback(
        name=data.name,
        email=data.email,
        message=data.message,
    )
    db.add(feedback)
    await db.commit()
    await db.refresh(feedback)
    return feedback


@router.get("/api/feedbacks", response_model=List[FeedbackResponse])
async def list_feedbacks(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(100, ge=1, le=1000),
):
    result = await db.execute(
        select(Feedback).order_by(Feedback.created_at.desc()).limit(limit)
    )
    return result.scalars().all()


@router.get("/api/feedbacks/count")
async def feedbacks_count(
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(func.count()).select_from(Feedback))
    return {"count": result.scalar()}


@router.delete("/api/feedbacks/{feedback_id}")
async def delete_feedback(
    feedback_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Feedback).where(Feedback.id == feedback_id))
    feedback = result.scalar_one_or_none()
    if not feedback:
        raise HTTPException(status_code=404, detail="Feedback not found")
    await db.delete(feedback)
    await db.commit()
    return {"ok": True}
