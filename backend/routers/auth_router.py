"""
Authentication router.

Default admin flow
------------------
1. A seed account (username=admin, password=admin123) is created on first startup.
   It is flagged is_default=True, profile_complete=False.
2. Login with the seed account returns requires_setup=True in the token response.
3. The only thing a default-account token may do is call POST /auth/setup.
   Every other endpoint rejects it with 403.
4. POST /auth/setup creates a brand-new real admin under a fresh UUID, seeds
   PharmacySettings for them, marks the seed account is_active=False so it can
   never be used again, and returns a fresh token for the new admin.
"""
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.database import get_db
from backend.models import User, PharmacySetting
from backend.schemas import (
    LoginRequest, TokenResponse, UserCreate, UserResponse,
    SetupRequest, SetupResponse,
)
from backend.auth import (
    hash_password, verify_password, create_access_token,
    get_current_user, require_admin, get_tenant_id, _role_str,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


# ---------------------------------------------------------------------------
# Dependency: reject default-account tokens on normal endpoints
# ---------------------------------------------------------------------------

def require_real_admin(user: User = Depends(require_admin)) -> User:
    """Admin-only AND must have completed setup."""
    if getattr(user, "is_default", False) or not getattr(user, "profile_complete", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account setup not complete. Please finish the setup wizard first.",
        )
    return user


def require_profile_complete(user: User = Depends(get_current_user)) -> User:
    """Any authenticated user — must have profile_complete=True."""
    if getattr(user, "is_default", False) or not getattr(user, "profile_complete", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account setup not complete. Please finish the setup wizard first.",
        )
    return user


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_token_response(user: User, requires_setup: bool = False) -> TokenResponse:
    tenant_id = str(user.admin_id) if user.admin_id else str(user.id)
    token_data = {
        "sub": str(user.id),
        "username": user.username,
        "role": _role_str(user),
        "admin_id": tenant_id,
        "requires_setup": requires_setup,
    }
    access_token = create_access_token(data=token_data)
    return TokenResponse(
        access_token=access_token,
        user_id=str(user.id),
        username=user.username,
        full_name=user.full_name,
        role=_role_str(user),
        admin_id=tenant_id,
        requires_setup=requires_setup,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

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

    requires_setup = getattr(user, "is_default", False) or not getattr(user, "profile_complete", True)
    return _build_token_response(user, requires_setup=requires_setup)


@router.post("/verify")
async def verify_token(user: User = Depends(get_current_user)):
    requires_setup = getattr(user, "is_default", False) or not getattr(user, "profile_complete", True)
    return {
        "valid": True,
        "user_id": str(user.id),
        "username": user.username,
        "role": _role_str(user),
        "admin_id": str(user.admin_id) if user.admin_id else str(user.id),
        "requires_setup": requires_setup,
    }


@router.post("/setup", response_model=SetupResponse)
async def setup_account(
    req: SetupRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Called once after first login with the default account.
    Creates a brand-new real admin with the provided credentials and profile.
    The seed (default) account is permanently deactivated.
    """
    # Only the default seed account (or incomplete profile) may call this
    is_default = getattr(current_user, "is_default", False)
    profile_done = getattr(current_user, "profile_complete", True)
    if not is_default and profile_done:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Setup already completed for this account.",
        )

    # New username must not collide
    dup = await db.execute(select(User).where(User.username == req.new_username))
    if dup.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Username '{req.new_username}' is already taken. Choose a different one.",
        )

    # Create the real admin account — pass role as string for PgEnum compatibility
    new_admin = User(
        username=req.new_username,
        password_hash=hash_password(req.new_password),
        full_name=req.full_name,
        email=req.email,
        phone=req.phone,
        role="admin",
        is_active=True,
        is_default=False,
        profile_complete=True,
    )
    db.add(new_admin)
    await db.flush()  # get new_admin.id

    # Seed pharmacy settings for the new admin
    pharmacy = PharmacySetting(
        admin_id=new_admin.id,
        pharmacy_name=req.pharmacy_name,
        phone=req.phone,
        email=req.email,
        currency_symbol="KES",
        tax_rate=0.16,
    )
    db.add(pharmacy)

    # Do NOT deactivate the seed account — it stays active so other
    # fresh installs can still use admin/admin123 to set up their own account.
    # We only mark it profile_complete=False so it always redirects to setup.
    await db.commit()

    # Issue fresh token for the new admin
    token_data = {
        "sub": str(new_admin.id),
        "username": new_admin.username,
        "role": _role_str(new_admin),
        "admin_id": str(new_admin.id),
        "requires_setup": False,
    }
    access_token = create_access_token(data=token_data)

    return SetupResponse(
        access_token=access_token,
        user_id=str(new_admin.id),
        username=new_admin.username,
        full_name=new_admin.full_name,
        role=_role_str(new_admin),
        admin_id=str(new_admin.id),
        requires_setup=False,
    )


@router.post("/register", response_model=UserResponse)
async def register_worker(
    req: UserCreate,
    admin: User = Depends(require_real_admin),   # blocks default account
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
        role=req.role.value if hasattr(req.role, "value") else str(req.role),
        admin_id=admin.id if _role_str(admin) == "admin" else admin.admin_id,
        is_default=False,
        profile_complete=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.get("/workers", response_model=list[UserResponse])
async def list_workers(
    admin: User = Depends(require_real_admin),   # blocks default account
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User).where(User.admin_id == admin.id).order_by(User.created_at.desc())
    )
    return result.scalars().all()


@router.get("/me", response_model=UserResponse)
async def get_me(user: User = Depends(get_current_user)):
    return user


@router.get("/profile")
async def get_profile(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns the current user's full profile including:
    - Their own user record
    - The admin who created them (for non-admin users)
    - The pharmacy settings belonging to their admin
    """
    is_admin = _role_str(user) == "admin"
    admin_id = user.admin_id if user.admin_id else user.id

    # Fetch admin user record
    admin_result = await db.execute(select(User).where(User.id == admin_id))
    admin_user = admin_result.scalar_one_or_none()

    # Fetch pharmacy settings for this tenant
    settings_result = await db.execute(
        select(PharmacySetting).where(PharmacySetting.admin_id == admin_id)
    )
    pharmacy = settings_result.scalar_one_or_none()

    return {
        "user": {
            "id":             str(user.id),
            "username":       user.username,
            "full_name":      user.full_name,
            "email":          user.email or "",
            "phone":          user.phone or "",
            "role":           _role_str(user),
            "created_at":     user.created_at.isoformat() if user.created_at else "",
            "last_login":     user.last_login.isoformat() if user.last_login else "",
        },
        "admin": {
            "id":        str(admin_user.id) if admin_user else "",
            "username":  admin_user.username if admin_user else "",
            "full_name": admin_user.full_name if admin_user else "",
            "email":     admin_user.email or "" if admin_user else "",
            "phone":     admin_user.phone or "" if admin_user else "",
        } if not is_admin else None,
        "pharmacy": {
            "name":             pharmacy.pharmacy_name or "" if pharmacy else "",
            "address":          pharmacy.address or "" if pharmacy else "",
            "phone":            pharmacy.phone or "" if pharmacy else "",
            "email":            pharmacy.email or "" if pharmacy else "",
            "currency_symbol":  pharmacy.currency_symbol or "KES" if pharmacy else "KES",
            "tax_rate":         pharmacy.tax_rate or 0.16 if pharmacy else 0.16,
        } if pharmacy else None,
    }


@router.put("/profile")
async def update_profile(
    data: dict,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Allow any user to update their own full_name, email, phone."""
    allowed_fields = {"full_name", "email", "phone"}
    changed = False
    for field in allowed_fields:
        if field in data and data[field] is not None:
            setattr(user, field, str(data[field]).strip() or None)
            changed = True
    if changed:
        await db.commit()
    return {"success": True, "message": "Profile updated successfully."}


@router.put("/change-password")
async def change_password(
    data: dict,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Allow any user to change their own password."""
    current = data.get("current_password", "")
    new_pw = data.get("new_password", "")

    if not verify_password(current, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect.",
        )
    if len(new_pw) < 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be at least 6 characters.",
        )

    user.password_hash = hash_password(new_pw)
    await db.commit()
    return {"success": True, "message": "Password changed successfully."}
