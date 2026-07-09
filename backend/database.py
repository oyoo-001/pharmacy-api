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

    await _seed_default_admin()


async def _seed_default_admin():
    """Ensure exactly one default admin account (admin/admin123) exists.
    This account is marked is_default=True and profile_complete=False.
    It is purely a setup gateway — real operations require completing setup first.
    """
    from backend.models import User, UserRole
    from backend.auth import hash_password

    async with async_session() as db:
        result = await db.execute(
            select(User).where(User.username == "admin")
        )
        existing = result.scalar_one_or_none()

        if existing is None:
            default_admin = User(
                username="admin",
                password_hash=hash_password(ADMIN_DEFAULT_PASSWORD),
                full_name="Default Admin",
                role=UserRole.admin,
                is_active=True,
                is_default=True,
                profile_complete=False,
            )
            db.add(default_admin)
            await db.commit()
            log.info("Default admin account seeded (admin / %s).", ADMIN_DEFAULT_PASSWORD)
        else:
            # Ensure existing seed account has the new columns set correctly
            changed = False
            if not hasattr(existing, 'is_default') or existing.is_default is None:
                existing.is_default = True
                changed = True
            if not hasattr(existing, 'profile_complete') or existing.profile_complete is None:
                existing.profile_complete = False
                changed = True
            if changed:
                await db.commit()
            log.info("Default admin account already exists.")
