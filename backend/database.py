from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import text, select
from sqlalchemy.orm import DeclarativeBase
from backend.config import DATABASE_URL, ADMIN_DEFAULT_PASSWORD, log

log.info("Creating database engine…")
engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    log.info("Testing database connection…")
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        log.info("Database connection established successfully.")
    except Exception as e:
        log.error("Database connection FAILED: %s", e)
        raise

    log.info("Running schema migration (create_all)…")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info("Migration complete — all tables up to date.")

    # Add new columns to existing tables if they were deployed before this version
    await _run_column_migrations()
    await _seed_default_admin()


async def _run_column_migrations():
    """
    Safe migrations for columns and types added after initial deployment.
    All statements use IF NOT EXISTS / OR REPLACE so they are idempotent.
    """
    async with engine.begin() as conn:
        # Ensure all enum types exist with the correct names
        # (DO $$ block is idempotent — creates only if missing)
        enum_migrations = [
            ("user_role",       "admin, pharmacist, cashier, worker"),
            ("transactiontype", "purchase, sale, return, adjustment"),
            ("paymentmethod",   "cash, mobile_money, credit"),
            ("paymentstatus",   "completed, refunded, pending"),
            ("postatus",        "pending, approved, received, cancelled"),
        ]
        for type_name, values in enum_migrations:
            quoted = ", ".join(f"'{v}'" for v in values.split(", "))
            # Create type if it doesn't exist
            stmt = f"""
                DO $$ BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = '{type_name}') THEN
                        CREATE TYPE {type_name} AS ENUM ({quoted});
                    END IF;
                END $$;
            """
            try:
                await conn.execute(text(stmt))
                log.info("Enum type ensured: %s", type_name)
            except Exception as e:
                log.warning("Enum type migration skipped (%s): %s", type_name, e)

        # Add new enum values to existing user_role type (idempotent)
        new_role_values = ["pharmacist", "cashier"]
        for val in new_role_values:
            add_stmt = f"""
                DO $$ BEGIN
                    ALTER TYPE user_role ADD VALUE IF NOT EXISTS '{val}';
                EXCEPTION WHEN others THEN NULL;
                END $$;
            """
            try:
                await conn.execute(text(add_stmt))
                log.info("Enum value ensured: user_role.%s", val)
            except Exception as e:
                log.warning("Enum value add skipped (%s): %s", val, e)

        # Add new columns to users table
        column_migrations = [
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_default BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_complete BOOLEAN NOT NULL DEFAULT TRUE",
            # medicines.session_token — added in v1.0.3 for mobile scan sessions
            "ALTER TABLE medicines ADD COLUMN IF NOT EXISTS session_token VARCHAR(200)",
            # Create index on session_token if column was just added
            "CREATE INDEX IF NOT EXISTS idx_medicines_session_token ON medicines(session_token)",
            # payment_settings table — per-tenant Paystack credentials
            """CREATE TABLE IF NOT EXISTS payment_settings (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                admin_id UUID NOT NULL UNIQUE REFERENCES users(id),
                paystack_secret_key VARCHAR(200),
                paystack_public_key VARCHAR(200),
                is_live BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )""",
            "CREATE INDEX IF NOT EXISTS idx_payment_settings_admin ON payment_settings(admin_id)",
            # mpesa_transactions table
            """CREATE TABLE IF NOT EXISTS mpesa_transactions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                admin_id UUID NOT NULL REFERENCES users(id),
                reference VARCHAR(100) NOT NULL UNIQUE,
                email VARCHAR(200) NOT NULL,
                phone_number VARCHAR(50) NOT NULL,
                amount FLOAT NOT NULL,
                currency VARCHAR(10) DEFAULT 'KES' NOT NULL,
                status VARCHAR(20) DEFAULT 'pending' NOT NULL,
                paystack_data JSONB,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )""",
            "CREATE INDEX IF NOT EXISTS idx_mpesa_tx_admin ON mpesa_transactions(admin_id)",
            "CREATE INDEX IF NOT EXISTS idx_mpesa_tx_ref ON mpesa_transactions(reference)",
            # mobile_sync_sessions table — for /addmedicine page
            """CREATE TABLE IF NOT EXISTS mobile_sync_sessions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                token VARCHAR(200) NOT NULL UNIQUE,
                admin_id UUID NOT NULL REFERENCES users(id),
                status VARCHAR(20) DEFAULT 'pending' NOT NULL,
                medicine_id UUID REFERENCES medicines(id),
                error_message TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )""",
            "CREATE INDEX IF NOT EXISTS idx_sync_sessions_token ON mobile_sync_sessions(token)",
            "CREATE INDEX IF NOT EXISTS idx_sync_sessions_admin ON mobile_sync_sessions(admin_id)",
        ]
        for stmt in column_migrations:
            try:
                await conn.execute(text(stmt))
                log.info("Column migration OK: %s", stmt[:60])
            except Exception as e:
                log.warning("Column migration skipped (%s): %s", stmt[:60], e)

    log.info("All migrations complete.")


async def _seed_default_admin():
    """Ensure exactly one default admin account (admin/admin123) exists.
    Uses raw SQL to avoid SQLAlchemy enum type cast issues with PostgreSQL.
    """
    from backend.auth import hash_password

    password_hash = hash_password(ADMIN_DEFAULT_PASSWORD)

    async with engine.begin() as conn:
        # Check if admin exists
        result = await conn.execute(
            text("SELECT id, is_default, profile_complete FROM users WHERE username = 'admin'")
        )
        existing = result.fetchone()

        if existing is None:
            await conn.execute(text("""
                INSERT INTO users (
                    id, username, password_hash, full_name,
                    role, is_active, is_default, profile_complete,
                    created_at, updated_at
                ) VALUES (
                    gen_random_uuid(), 'admin', :pwd, 'Default Admin',
                    'admin'::user_role, true, true, false,
                    NOW(), NOW()
                )
            """), {"pwd": password_hash})
            log.info("Default admin account seeded (admin / %s).", ADMIN_DEFAULT_PASSWORD)
        else:
            # Ensure the seed flags are correct
            needs_update = (not existing.is_default) or existing.profile_complete
            if needs_update:
                await conn.execute(text("""
                    UPDATE users
                    SET is_default = true, profile_complete = false
                    WHERE username = 'admin'
                      AND (is_default = false OR profile_complete = true)
                """))
                log.info("Default admin account flags corrected.")
            else:
                log.info("Default admin account already correctly configured.")
