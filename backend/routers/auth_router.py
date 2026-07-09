import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.database import get_db
from backend.models import User, UserRole
from backend.schemas import LoginRequest, TokenResponse, UserCreate, UserResponse
from backend.auth import (
    hash_password, verify_password, create_access_token,
    get_current_user, require_admin, get_tenant_id,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == req.username))
    user = result.scalar_one_or_none()

    if user is None or not verify_password(req.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account deactivated",
        )

    user.last_login = datetime.now(timezone.utc)
    await db.commit()

    token_data = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role.value,
        "admin_id": str(user.admin_id) if user.admin_id else str(user.id),
    }
    access_token = create_access_token(data=token_data)

    return TokenResponse(
        access_token=access_token,
        user_id=str(user.id),
        username=user.username,
        full_name=user.full_name,
        role=user.role,
        admin_id=str(user.admin_id) if user.admin_id else str(user.id),
    )


@router.post("/verify")
async def verify_token(user: User = Depends(get_current_user)):
    return {
        "valid": True,
        "user_id": str(user.id),
        "username": user.username,
        "role": user.role.value,
        "admin_id": str(user.admin_id) if user.admin_id else str(user.id),
    }


@router.post("/register", response_model=UserResponse)
async def register_worker(
    req: UserCreate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(select(User).where(User.username == req.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Username already taken")

    user = User(
        username=req.username,
        password_hash=hash_password(req.password),
        full_name=req.full_name,
        email=req.email,
        phone=req.phone,
        role=req.role,
        admin_id=admin.id if admin.role == UserRole.admin else admin.admin_id,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.get("/workers", response_model=list[UserResponse])
async def list_workers(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User).where(User.admin_id == admin.id).order_by(User.created_at.desc())
    )
    return result.scalars().all()


@router.get("/me", response_model=UserResponse)
async def get_me(user: User = Depends(get_current_user)):
    return user
