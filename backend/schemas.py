import uuid
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field
from typing import Optional, Union
from backend.models import UserRole


# ---------- Auth ----------

class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    """
    role is str (not UserRole) because PostgreSQL ENUM returns plain strings
    via asyncpg and we don't want Pydantic to reject "admin"/"worker" as str.
    """
    access_token: str
    token_type: str = "bearer"
    user_id: str
    username: str
    full_name: str
    role: str                      # plain str — "admin" or "worker"
    admin_id: Optional[str] = None
    requires_setup: bool = False


# Setup wizard — called once to graduate the default account into a real admin
class SetupRequest(BaseModel):
    new_username: str = Field(min_length=3, max_length=100)
    new_password: str = Field(min_length=8)
    full_name: str = Field(min_length=1, max_length=200)
    pharmacy_name: str = Field(min_length=1, max_length=200)
    phone: Optional[str] = None
    email: Optional[str] = None


class SetupResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    username: str
    full_name: str
    role: str
    admin_id: str
    requires_setup: bool = False


class TokenData(BaseModel):
    user_id: Optional[str] = None
    username: Optional[str] = None
    role: Optional[str] = None
    admin_id: Optional[str] = None


# ---------- Users ----------

class UserCreate(BaseModel):
    """Input schema — validate role as enum."""
    username: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=6)
    full_name: str = Field(min_length=1, max_length=200)
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    role: UserRole = UserRole.worker


class UserResponse(BaseModel):
    """
    Output schema — role is str because PgEnum returns str from asyncpg.
    is_default and profile_complete may be None on old rows before migration;
    default to safe values.
    """
    id: uuid.UUID
    username: str
    full_name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    role: str
    admin_id: Optional[uuid.UUID] = None
    is_active: bool
    is_default: bool = False
    profile_complete: bool = True
    created_at: datetime
    last_login: Optional[datetime] = None

    model_config = {"from_attributes": True}


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    is_active: Optional[bool] = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=6)


# ---------- Dashboard ----------

class DashboardStats(BaseModel):
    total_medicines: int
    low_stock_count: int
    total_sales_today: float
    total_sales_month: float
    active_suppliers: int
    total_prescriptions: int
    total_workers: int
    expired_medicines: int
