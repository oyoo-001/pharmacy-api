"""
Messaging router — admin-user private chat with real-time polling support.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from sqlalchemy.orm import selectinload
from pydantic import BaseModel

from backend.database import get_db
from backend.models import User, Conversation, Message
from backend.auth import get_current_user, get_tenant_id, require_profile_complete

router = APIRouter(prefix="/messaging", tags=["Messaging"])


# ---------- Schemas ----------

class MessageOut(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    sender_id: uuid.UUID
    sender_role: str
    content: str
    is_read: bool
    created_at: datetime

    class Config:
        from_attributes = True


class ConversationOut(BaseModel):
    id: uuid.UUID
    admin_id: uuid.UUID
    user_id: uuid.UUID
    user_name: str = ""
    user_role: str = ""
    subject: Optional[str] = None
    status: str
    last_message: Optional[str] = None
    last_message_at: Optional[datetime] = None
    unread_count: int = 0
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SendMessageIn(BaseModel):
    content: str


class UnreadCountOut(BaseModel):
    count: int


# ---------- Helpers ----------

async def _get_or_create_conversation(db: AsyncSession, admin_id: uuid.UUID, user_id: uuid.UUID) -> Conversation:
    result = await db.execute(
        select(Conversation).where(
            Conversation.admin_id == admin_id,
            Conversation.user_id == user_id,
        )
    )
    conv = result.scalar_one_or_none()
    if conv:
        return conv
    conv = Conversation(admin_id=admin_id, user_id=user_id)
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    return conv


async def _enrich_conversation(db: AsyncSession, conv: Conversation, current_user_id: uuid.UUID, tenant_id: uuid.UUID) -> dict:
    # Get the other user's info
    other_id = conv.user_id if conv.admin_id == current_user_id else conv.admin_id
    result = await db.execute(select(User).where(User.id == other_id))
    other_user = result.scalar_one_or_none()

    # Last message
    msg_result = await db.execute(
        select(Message).where(Message.conversation_id == conv.id)
        .order_by(Message.created_at.desc()).limit(1)
    )
    last_msg = msg_result.scalar_one_or_none()

    # Unread count (for the current user)
    unread_result = await db.execute(
        select(func.count()).select_from(Message).where(
            Message.conversation_id == conv.id,
            Message.is_read == False,
            Message.sender_id != current_user_id,
        )
    )
    unread = unread_result.scalar() or 0

    return {
        "id": conv.id,
        "admin_id": conv.admin_id,
        "user_id": conv.user_id,
        "user_name": other_user.full_name if other_user else "Unknown",
        "user_role": other_user.role if other_user else "",
        "subject": conv.subject,
        "status": conv.status,
        "last_message": last_msg.content[:100] if last_msg else None,
        "last_message_at": last_msg.created_at if last_msg else conv.updated_at,
        "unread_count": unread,
        "created_at": conv.created_at,
        "updated_at": conv.updated_at,
    }


# ---------- Routes ----------

@router.get("/conversations", response_model=List[ConversationOut])
async def list_conversations(
    user: User = Depends(require_profile_complete),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = get_tenant_id(user)

    if user.role == "admin":
        result = await db.execute(
            select(Conversation).where(Conversation.admin_id == tenant_id)
            .order_by(Conversation.updated_at.desc())
        )
    else:
        result = await db.execute(
            select(Conversation).where(
                Conversation.admin_id == tenant_id,
                Conversation.user_id == user.id,
            )
        )
    convs = result.scalars().all()
    return [await _enrich_conversation(db, c, user.id, tenant_id) for c in convs]


@router.post("/conversations", response_model=ConversationOut)
async def get_or_create_conversation_route(
    user: User = Depends(require_profile_complete),
    db: AsyncSession = Depends(get_db),
):
    """Get or create a conversation between the current user and their admin."""
    tenant_id = get_tenant_id(user)
    admin_id = tenant_id if user.role == "admin" else tenant_id
    user_id = user.id

    # Staff user's admin is the tenant_id
    if user.role != "admin":
        admin_id = tenant_id
        user_id = user.id
    else:
        # Admin chatting with themselves? Use a different endpoint
        raise HTTPException(status_code=400, detail="Admin must specify a user_id")

    conv = await _get_or_create_conversation(db, admin_id, user_id)
    return await _enrich_conversation(db, conv, user.id, tenant_id)


@router.get("/conversations/unread-count", response_model=UnreadCountOut)
async def unread_count(
    user: User = Depends(require_profile_complete),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = get_tenant_id(user)
    query = (
        select(func.count()).select_from(Message)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(
            Conversation.admin_id == tenant_id,
            Message.is_read == False,
            Message.sender_id != user.id,
        )
    )
    result = await db.execute(query)
    return {"count": result.scalar() or 0}


@router.get("/conversations/{conversation_id}/messages", response_model=List[MessageOut])
async def get_messages(
    conversation_id: uuid.UUID,
    user: User = Depends(require_profile_complete),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
    before: Optional[str] = Query(None, description="ISO datetime — paginate messages before this timestamp"),
):
    tenant_id = get_tenant_id(user)

    # Verify the conversation belongs to this tenant
    conv_result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.admin_id == tenant_id,
        )
    )
    conv = conv_result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    query = select(Message).where(Message.conversation_id == conversation_id)
    if before:
        query = query.where(Message.created_at < before.replace("Z", "+00:00"))
    query = query.order_by(Message.created_at.desc()).limit(limit)

    result = await db.execute(query)
    messages = result.scalars().all()
    messages.reverse()  # oldest first
    return messages


@router.post("/conversations/{conversation_id}/messages", response_model=MessageOut)
async def send_message(
    conversation_id: uuid.UUID,
    data: SendMessageIn,
    user: User = Depends(require_profile_complete),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = get_tenant_id(user)

    conv_result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.admin_id == tenant_id,
        )
    )
    conv = conv_result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if user.id not in (conv.admin_id, conv.user_id):
        raise HTTPException(status_code=403, detail="Not a participant in this conversation")

    msg = Message(
        conversation_id=conversation_id,
        sender_id=user.id,
        sender_role=user.role,
        content=data.content,
    )
    db.add(msg)
    conv.updated_at = datetime.now(timezone.utc)

    # Notify the admin if a worker sent the message
    if user.id != conv.admin_id:
        from backend.routers.notifications_router import create_notification
        sender_name = user.full_name or user.username or "Worker"
        await create_notification(
            db, conv.admin_id,
            title="New Message",
            message=f"{sender_name}: {data.content[:80]}",
            ntype="message",
        )

    await db.commit()
    await db.refresh(msg)
    return msg


@router.put("/conversations/{conversation_id}/read")
async def mark_read(
    conversation_id: uuid.UUID,
    user: User = Depends(require_profile_complete),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = get_tenant_id(user)

    conv_result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.admin_id == tenant_id,
        )
    )
    conv = conv_result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    await db.execute(
        select(Message).where(
            Message.conversation_id == conversation_id,
            Message.is_read == False,
            Message.sender_id != user.id,
        )
    )
    await db.execute(
        Message.__table__.update().where(
            Message.conversation_id == conversation_id,
            Message.is_read == False,
            Message.sender_id != user.id,
        ).values(is_read=True)
    )
    await db.commit()
    return {"ok": True}
