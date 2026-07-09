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
    Safe ALTER TABLE migrations for columns added after initial deployment.
    Each statement uses IF NOT EXISTS so they are idempotent.
    """
    migrations = [
        # Added in setup-wizard release
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_default BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_complete BOOLEAN NOT NULL DEFAULT TRUE",
    ]
    async with engine.begin() as conn:
        for stmt in migrations:
            try:
                await conn.execute(text(stmt))
                log.info("Migration OK: %s", stmt[:60])
            except Exception as e:
                log.warning("Migration skipped (%s): %s", stmt[:60], e)
    log.info("Column migrations complete.")


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
