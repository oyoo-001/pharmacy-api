import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Integer, Float, Boolean, Date, DateTime, Text,
    ForeignKey, JSON, UniqueConstraint, Index
)
from sqlalchemy.dialects.postgresql import UUID, ENUM as PgEnum
from sqlalchemy.orm import relationship
from backend.database import Base


def utcnow():
    return datetime.now(timezone.utc)


# ---------- Enums ----------
from enum import Enum as PyEnum


class UserRole(str, PyEnum):
    admin = "admin"
    pharmacist = "pharmacist"
    cashier = "cashier"
    worker = "worker"


class TransactionType(str, PyEnum):
    purchase = "purchase"
    sale = "sale"
    return_ = "return"
    adjustment = "adjustment"


class PaymentMethod(str, PyEnum):
    cash = "cash"
    mobile_money = "mobile_money"
    credit = "credit"


class PaymentStatus(str, PyEnum):
    completed = "completed"
    refunded = "refunded"
    pending = "pending"


class POStatus(str, PyEnum):
    pending = "pending"
    approved = "approved"
    received = "received"
    cancelled = "cancelled"


# ---------- PostgreSQL ENUM helpers ----------
# Use create_type=False so SQLAlchemy never tries to CREATE TYPE —
# the types already exist in the DB. The name= must match exactly
# what PostgreSQL has (check with: SELECT typname FROM pg_type WHERE typcategory='E')

def _pg_enum(*values, name: str):
    """Return a PostgreSQL ENUM column type that references an existing DB type."""
    return PgEnum(*values, name=name, create_type=False)


# ---------- Models ----------

class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String(100), unique=True, nullable=False, index=True)
    password_hash = Column(Text, nullable=False)
    full_name = Column(String(200), nullable=False)
    email = Column(String(200))
    phone = Column(String(50))
    role = Column(_pg_enum("admin", "pharmacist", "cashier", "worker", name="user_role"), default=UserRole.worker)
    admin_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    is_active = Column(Boolean, default=True)
    is_default = Column(Boolean, default=False)       # True only for the seed admin account
    profile_complete = Column(Boolean, default=True)  # False until setup wizard is done
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    last_login = Column(DateTime(timezone=True))

    # relationships
    workers = relationship("User", backref="admin", remote_side=[id])
    settings = relationship("PharmacySetting", uselist=False, back_populates="admin")


class PharmacySetting(Base):
    __tablename__ = "pharmacy_settings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    admin_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), unique=True, nullable=False)
    pharmacy_name = Column(String(200), default="Kevin Odongo Pharmacy")
    address = Column(Text)
    phone = Column(String(50))
    email = Column(String(200))
    tax_rate = Column(Float, default=0.16)
    receipt_footer = Column(Text)
    logo_path = Column(Text)
    currency_symbol = Column(String(10), default="KES")
    # ── Receipt design fields ────────────────────────────────────────────
    receipt_header = Column(Text)                       # custom header text (e.g. "Welcome!")
    receipt_notes = Column(Text)                        # notes section above footer
    receipt_accent_color = Column(String(20), default="#6366f1")  # theme color
    receipt_width = Column(String(20), default="80mm")  # 58mm | 80mm | A4
    receipt_show_tax = Column(default=True)              # show/hide tax line
    receipt_show_qr = Column(default=False)              # show/hide QR code
    # ── Notification settings ──────────────────────────────────────────
    notifications_enabled = Column(Boolean, default=True)  # master toggle
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    admin = relationship("User", back_populates="settings")


class Medicine(Base):
    __tablename__ = "medicines"

    id            = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    admin_id      = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    # session_token links to a MobileSyncSession that created this record via scan
    session_token = Column(String(200), nullable=True, index=True)
    name          = Column(String(200), nullable=False)
    category      = Column(String(100))
    batch_number  = Column(String(100))
    expiry_date   = Column(Date)
    buying_price  = Column(Float, default=0.0)
    selling_price = Column(Float, default=0.0)
    quantity      = Column(Integer, default=0)
    reorder_level = Column(Integer, default=10)
    description   = Column(Text)
    is_active     = Column(Boolean, default=True)
    created_at    = Column(DateTime(timezone=True), default=utcnow)
    updated_at    = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("admin_id", "batch_number"),
        Index("idx_medicines_name",     "admin_id", "name"),
        Index("idx_medicines_category", "admin_id", "category"),
        Index("idx_medicines_expiry",   "admin_id", "expiry_date"),
        Index("idx_medicines_stock",    "admin_id", "quantity"),
    )


class MobileSyncSession(Base):
    """
    Tracks a single 'scan-to-add' session initiated from the /addmedicine webpage.

    Lifecycle:  pending → completed | failed
    The desktop UI polls GET /api/sync/check/<token> and gets the full
    medicine record back once status = 'completed'.
    """
    __tablename__ = "mobile_sync_sessions"

    id            = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token         = Column(String(200), unique=True, nullable=False, index=True)
    admin_id      = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    status        = Column(String(20), default="pending", nullable=False)
                        # pending | completed | failed
    medicine_id   = Column(UUID(as_uuid=True), ForeignKey("medicines.id"), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at    = Column(DateTime(timezone=True), default=utcnow)
    updated_at    = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Supplier(Base):
    __tablename__ = "suppliers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    admin_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    contact_person = Column(String(200))
    phone = Column(String(50))
    email = Column(String(200))
    address = Column(Text)
    tax_id = Column(String(100))
    payment_terms = Column(String(100))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class InventoryTransaction(Base):
    __tablename__ = "inventory_transactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    admin_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    medicine_id = Column(UUID(as_uuid=True), ForeignKey("medicines.id"), nullable=False)
    transaction_type = Column(_pg_enum("purchase", "sale", "return", "adjustment", name="transaction_type"), nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Float)
    total_price = Column(Float)
    reference_id = Column(UUID(as_uuid=True))
    reference_type = Column(String(50))
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class Sale(Base):
    __tablename__ = "sales"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    admin_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    invoice_number = Column(String(50), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    customer_name = Column(String(200))
    customer_phone = Column(String(50))
    subtotal = Column(Float)
    tax = Column(Float)
    discount = Column(Float)
    total = Column(Float)
    payment_method = Column(_pg_enum("cash", "mobile_money", "credit", name="payment_method"), default=PaymentMethod.cash)
    payment_status = Column(_pg_enum("completed", "refunded", "pending", name="payment_status"), default=PaymentStatus.completed)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    items = relationship("SaleItem", back_populates="sale", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("admin_id", "invoice_number"),
        Index("idx_sales_invoice", "admin_id", "invoice_number"),
        Index("idx_sales_created", "admin_id", "created_at"),
    )


class SaleItem(Base):
    __tablename__ = "sale_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    admin_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    sale_id = Column(UUID(as_uuid=True), ForeignKey("sales.id"), nullable=False)
    medicine_id = Column(UUID(as_uuid=True), ForeignKey("medicines.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Float)
    total_price = Column(Float)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    sale = relationship("Sale", back_populates="items")


class Prescription(Base):
    __tablename__ = "prescriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    admin_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    patient_name = Column(String(200), nullable=False)
    patient_age = Column(Integer)
    patient_gender = Column(String(20))
    doctor_name = Column(String(200))
    doctor_phone = Column(String(50))
    diagnosis = Column(Text)
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    items = relationship("PrescriptionItem", back_populates="prescription", cascade="all, delete-orphan")


class PrescriptionItem(Base):
    __tablename__ = "prescription_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    admin_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    prescription_id = Column(UUID(as_uuid=True), ForeignKey("prescriptions.id"), nullable=False)
    medicine_id = Column(UUID(as_uuid=True), ForeignKey("medicines.id"), nullable=False)
    dosage = Column(Text)
    duration = Column(Text)
    instructions = Column(Text)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    prescription = relationship("Prescription", back_populates="items")


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    admin_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    po_number = Column(String(50), nullable=False)
    supplier_id = Column(UUID(as_uuid=True), ForeignKey("suppliers.id"))
    order_date = Column(Date)
    expected_delivery = Column(Date)
    status = Column(_pg_enum("pending", "approved", "received", "cancelled", name="po_status"), default=POStatus.pending)
    total_amount = Column(Float)
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    items = relationship("PurchaseOrderItem", back_populates="purchase_order", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("admin_id", "po_number"),
    )


class PurchaseOrderItem(Base):
    __tablename__ = "purchase_order_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    admin_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    po_id = Column(UUID(as_uuid=True), ForeignKey("purchase_orders.id"), nullable=False)
    medicine_id = Column(UUID(as_uuid=True), ForeignKey("medicines.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Float)
    total_price = Column(Float)
    received_quantity = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    purchase_order = relationship("PurchaseOrder", back_populates="items")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    admin_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"))
    action = Column(String(100), nullable=False)
    entity_type = Column(String(50))
    entity_id = Column(UUID(as_uuid=True))
    details = Column(JSON)
    ip_address = Column(String(50))
    created_at = Column(DateTime(timezone=True), default=utcnow)


class Feedback(Base):
    __tablename__ = "feedbacks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), nullable=False)
    email = Column(String(200))
    message = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class ServerLog(Base):
    __tablename__ = "server_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    level = Column(String(20), default="INFO")
    message = Column(Text, nullable=False)
    ip_address = Column(String(50))
    path = Column(String(500))
    created_at = Column(DateTime(timezone=True), default=utcnow)


class MpesaTransaction(Base):
    """Records every M-Pesa STK Push attempt initiated via Paystack."""
    __tablename__ = "mpesa_transactions"

    id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    admin_id     = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    reference    = Column(String(100), unique=True, nullable=False, index=True)
    email        = Column(String(200), nullable=False)
    phone_number = Column(String(50),  nullable=False)
    amount       = Column(Float, nullable=False)
    currency     = Column(String(10),  default="KES", nullable=False)
    status       = Column(String(20),  default="pending", nullable=False)
    paystack_data = Column(JSON, nullable=True)
    created_at   = Column(DateTime(timezone=True), default=utcnow)
    updated_at   = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PaymentSettings(Base):
    """Per-admin Paystack credentials — each pharmacy has its own keys."""
    __tablename__ = "payment_settings"

    id                   = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    admin_id             = Column(UUID(as_uuid=True), ForeignKey("users.id"),
                                   unique=True, nullable=False, index=True)
    paystack_secret_key  = Column(String(200), nullable=True)
    paystack_public_key  = Column(String(200), nullable=True)
    is_live              = Column(Boolean, default=True)   # True = live, False = test
    created_at           = Column(DateTime(timezone=True), default=utcnow)
    updated_at           = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    admin = relationship("User", foreign_keys=[admin_id])


class OnlineSession(Base):
    __tablename__ = "online_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ip_address = Column(String(50), nullable=False, index=True)
    user_agent = Column(String(500))
    path = Column(String(200))
    last_ping = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    first_seen = Column(DateTime(timezone=True), default=utcnow)


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    admin_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    subject = Column(String(200))
    status = Column(String(20), default="active")
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("admin_id", "user_id", name="uq_conversation_pair"),
        Index("idx_conversations_admin_updated", "admin_id", "updated_at"),
    )


class Message(Base):
    __tablename__ = "messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    sender_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    sender_role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        Index("idx_messages_conversation_created", "conversation_id", "created_at"),
        Index("idx_messages_unread", "conversation_id", "is_read"),
    )


class AppUpdate(Base):
    """Published application update releases."""
    __tablename__ = "app_updates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version = Column(String(20), nullable=False, unique=True, index=True)
    bundle_url = Column(String(500), nullable=False)
    release_date = Column(DateTime(timezone=True), default=utcnow)
    release_notes = Column(Text, default="")
    min_app_version = Column(String(20), default="1.0.0")
    is_active = Column(Boolean, default=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)


class Notification(Base):
    """In-app notifications for the desktop sidebar bell."""
    __tablename__ = "notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    admin_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    message = Column(Text, nullable=False)
    type = Column(String(50), default="info")  # info | medicine | low_stock | message | alert
    is_read = Column(Boolean, default=False)
    link = Column(String(500))               # optional deep-link (e.g. /medicines)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        Index("idx_notifications_admin_read", "admin_id", "is_read"),
        Index("idx_notifications_admin_created", "admin_id", "created_at"),
    )
