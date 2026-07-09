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
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    admin = relationship("User", back_populates="settings")


class Medicine(Base):
    __tablename__ = "medicines"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    admin_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    category = Column(String(100))
    batch_number = Column(String(100))
    expiry_date = Column(Date)
    buying_price = Column(Float)
    selling_price = Column(Float)
    quantity = Column(Integer, default=0)
    reorder_level = Column(Integer, default=10)
    description = Column(Text)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("admin_id", "batch_number"),
        Index("idx_medicines_name", "admin_id", "name"),
        Index("idx_medicines_category", "admin_id", "category"),
        Index("idx_medicines_expiry", "admin_id", "expiry_date"),
        Index("idx_medicines_stock", "admin_id", "quantity"),
    )


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
    transaction_type = Column(_pg_enum("purchase", "sale", "return", "adjustment", name="transactiontype"), nullable=False)
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
    payment_method = Column(_pg_enum("cash", "mobile_money", "credit", name="paymentmethod"), default=PaymentMethod.cash)
    payment_status = Column(_pg_enum("completed", "refunded", "pending", name="paymentstatus"), default=PaymentStatus.completed)
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
    status = Column(_pg_enum("pending", "approved", "received", "cancelled", name="postatus"), default=POStatus.pending)
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
