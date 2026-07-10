-- ============================================================
-- Multi-Tenant Schema Migration for Kevin Odongo Pharmacy
-- Target: Aiven PostgreSQL
-- ============================================================

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================================
-- 1. CORE AUTH / TENANT TABLES
-- ============================================================

CREATE TYPE user_role AS ENUM ('admin', 'worker');

CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    username        VARCHAR(100) UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,                -- bcrypt hash
    full_name       VARCHAR(200) NOT NULL,
    email           VARCHAR(200),
    phone           VARCHAR(50),
    role            user_role NOT NULL DEFAULT 'worker',
    admin_id        UUID REFERENCES users(id),    -- NULL if admin, else points to owning admin
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    last_login      TIMESTAMPTZ
);

CREATE INDEX idx_users_admin_id ON users(admin_id);
CREATE INDEX idx_users_username ON users(username);
CREATE INDEX idx_users_role ON users(role);

-- ============================================================
-- 2. PHARMACY SETTINGS (per admin)
-- ============================================================

CREATE TABLE pharmacy_settings (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    admin_id        UUID NOT NULL REFERENCES users(id),
    pharmacy_name   VARCHAR(200) DEFAULT 'Kevin Odongo Pharmacy',
    address         TEXT,
    phone           VARCHAR(50),
    email           VARCHAR(200),
    tax_rate        REAL DEFAULT 0.16,
    receipt_footer  TEXT,
    logo_path       TEXT,
    currency_symbol VARCHAR(10) DEFAULT 'KES',
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(admin_id)
);

CREATE INDEX idx_pharmacy_settings_admin ON pharmacy_settings(admin_id);

-- ============================================================
-- 3. MEDICINES (per admin tenant)
-- ============================================================

CREATE TABLE medicines (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    admin_id        UUID NOT NULL REFERENCES users(id),
    name            VARCHAR(200) NOT NULL,
    category        VARCHAR(100),
    batch_number    VARCHAR(100),
    expiry_date     DATE,
    buying_price    REAL,
    selling_price   REAL,
    quantity        INTEGER DEFAULT 0,
    reorder_level   INTEGER DEFAULT 10,
    description     TEXT,
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(admin_id, batch_number)
);

CREATE INDEX idx_medicines_admin ON medicines(admin_id);
CREATE INDEX idx_medicines_name ON medicines(admin_id, name);
CREATE INDEX idx_medicines_category ON medicines(admin_id, category);
CREATE INDEX idx_medicines_expiry ON medicines(admin_id, expiry_date);
CREATE INDEX idx_medicines_stock ON medicines(admin_id, quantity);

-- ============================================================
-- 4. SUPPLIERS (per admin tenant)
-- ============================================================

CREATE TABLE suppliers (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    admin_id        UUID NOT NULL REFERENCES users(id),
    name            VARCHAR(200) NOT NULL,
    contact_person  VARCHAR(200),
    phone           VARCHAR(50),
    email           VARCHAR(200),
    address         TEXT,
    tax_id          VARCHAR(100),
    payment_terms   VARCHAR(100),
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_suppliers_admin ON suppliers(admin_id);

-- ============================================================
-- 5. INVENTORY TRANSACTIONS (per admin tenant)
-- ============================================================

CREATE TYPE transaction_type AS ENUM ('purchase', 'sale', 'return', 'adjustment');

CREATE TABLE inventory_transactions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    admin_id        UUID NOT NULL REFERENCES users(id),
    medicine_id     UUID NOT NULL REFERENCES medicines(id),
    transaction_type transaction_type NOT NULL,
    quantity        INTEGER NOT NULL,
    unit_price      REAL,
    total_price     REAL,
    reference_id    UUID,
    reference_type  VARCHAR(50),
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_invtx_admin ON inventory_transactions(admin_id);
CREATE INDEX idx_invtx_medicine ON inventory_transactions(admin_id, medicine_id);
CREATE INDEX idx_invtx_type ON inventory_transactions(admin_id, transaction_type);
CREATE INDEX idx_invtx_created ON inventory_transactions(admin_id, created_at);

-- ============================================================
-- 6. SALES (per admin tenant)
-- ============================================================

CREATE TYPE payment_method AS ENUM ('cash', 'mobile_money', 'credit');
CREATE TYPE payment_status AS ENUM ('completed', 'refunded', 'pending');

CREATE TABLE sales (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    admin_id        UUID NOT NULL REFERENCES users(id),
    invoice_number  VARCHAR(50) NOT NULL,
    user_id         UUID REFERENCES users(id),
    customer_name   VARCHAR(200),
    customer_phone  VARCHAR(50),
    subtotal        REAL,
    tax             REAL,
    discount        REAL,
    total           REAL,
    payment_method  payment_method DEFAULT 'cash',
    payment_status  payment_status DEFAULT 'completed',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(admin_id, invoice_number)
);

CREATE INDEX idx_sales_admin ON sales(admin_id);
CREATE INDEX idx_sales_invoice ON sales(admin_id, invoice_number);
CREATE INDEX idx_sales_created ON sales(admin_id, created_at);
CREATE INDEX idx_sales_customer ON sales(admin_id, customer_name);

-- ============================================================
-- 7. SALE ITEMS (per admin tenant via sales)
-- ============================================================

CREATE TABLE sale_items (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    admin_id        UUID NOT NULL REFERENCES users(id),
    sale_id         UUID NOT NULL REFERENCES sales(id),
    medicine_id     UUID NOT NULL REFERENCES medicines(id),
    quantity        INTEGER NOT NULL,
    unit_price      REAL,
    total_price     REAL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_saleitems_admin ON sale_items(admin_id);
CREATE INDEX idx_saleitems_sale ON sale_items(sale_id);
CREATE INDEX idx_saleitems_medicine ON sale_items(medicine_id);

-- ============================================================
-- 8. PRESCRIPTIONS (per admin tenant)
-- ============================================================

CREATE TABLE prescriptions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    admin_id        UUID NOT NULL REFERENCES users(id),
    patient_name    VARCHAR(200) NOT NULL,
    patient_age     INTEGER,
    patient_gender  VARCHAR(20),
    doctor_name     VARCHAR(200),
    doctor_phone    VARCHAR(50),
    diagnosis       TEXT,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_prescriptions_admin ON prescriptions(admin_id);
CREATE INDEX idx_prescriptions_patient ON prescriptions(admin_id, patient_name);

-- ============================================================
-- 9. PRESCRIPTION ITEMS (per admin tenant)
-- ============================================================

CREATE TABLE prescription_items (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    admin_id        UUID NOT NULL REFERENCES users(id),
    prescription_id UUID NOT NULL REFERENCES prescriptions(id),
    medicine_id     UUID NOT NULL REFERENCES medicines(id),
    dosage          TEXT,
    duration        TEXT,
    instructions    TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_prescitems_admin ON prescription_items(admin_id);
CREATE INDEX idx_prescitems_prescription ON prescription_items(prescription_id);

-- ============================================================
-- 10. PURCHASE ORDERS (per admin tenant)
-- ============================================================

CREATE TYPE po_status AS ENUM ('pending', 'approved', 'received', 'cancelled');

CREATE TABLE purchase_orders (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    admin_id        UUID NOT NULL REFERENCES users(id),
    po_number       VARCHAR(50) NOT NULL,
    supplier_id     UUID REFERENCES suppliers(id),
    order_date      DATE,
    expected_delivery DATE,
    status          po_status DEFAULT 'pending',
    total_amount    REAL,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(admin_id, po_number)
);

CREATE INDEX idx_po_admin ON purchase_orders(admin_id);

-- ============================================================
-- 11. PURCHASE ORDER ITEMS (per admin tenant)
-- ============================================================

CREATE TABLE purchase_order_items (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    admin_id        UUID NOT NULL REFERENCES users(id),
    po_id           UUID NOT NULL REFERENCES purchase_orders(id),
    medicine_id     UUID NOT NULL REFERENCES medicines(id),
    quantity        INTEGER NOT NULL,
    unit_price      REAL,
    total_price     REAL,
    received_quantity INTEGER DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_poitems_admin ON purchase_order_items(admin_id);
CREATE INDEX idx_poitems_po ON purchase_order_items(po_id);

-- ============================================================
-- 12. AUDIT LOG (optional, for security)
-- ============================================================

CREATE TABLE audit_log (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    admin_id        UUID REFERENCES users(id),
    user_id         UUID REFERENCES users(id),
    action          VARCHAR(100) NOT NULL,
    entity_type     VARCHAR(50),
    entity_id       UUID,
    details         JSONB,
    ip_address      VARCHAR(50),
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_audit_admin ON audit_log(admin_id);
CREATE INDEX idx_audit_user ON audit_log(user_id);
CREATE INDEX idx_audit_action ON audit_log(action);
CREATE INDEX idx_audit_created ON audit_log(created_at);

-- ============================================================
-- 13. CONVERSATIONS (admin-user messaging)
-- ============================================================

CREATE TABLE conversations (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    admin_id        UUID NOT NULL REFERENCES users(id),
    user_id         UUID NOT NULL REFERENCES users(id),
    subject         VARCHAR(200),
    status          VARCHAR(20) DEFAULT 'active',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(admin_id, user_id)
);

CREATE INDEX idx_conv_admin ON conversations(admin_id);
CREATE INDEX idx_conv_user ON conversations(user_id);
CREATE INDEX idx_conv_admin_updated ON conversations(admin_id, updated_at DESC);

-- ============================================================
-- 14. MESSAGES (within a conversation)
-- ============================================================

CREATE TABLE messages (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_id   UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    sender_id         UUID NOT NULL REFERENCES users(id),
    sender_role       VARCHAR(20) NOT NULL,
    content           TEXT NOT NULL,
    is_read           BOOLEAN DEFAULT FALSE,
    created_at        TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_msg_conversation ON messages(conversation_id, created_at);
CREATE INDEX idx_msg_unread ON messages(conversation_id, is_read);

-- ============================================================
-- SEED: Default admin user (password: admin123)
-- Hash will be generated by the backend on first run
-- ============================================================

-- The actual seed is done programmatically by the FastAPI
-- startup event to ensure bcrypt hashing.
