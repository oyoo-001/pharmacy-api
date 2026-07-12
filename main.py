import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from backend.config import APP_NAME, CORS_ORIGINS, PORT, log
from backend.database import init_db, async_session
from backend.models import ServerLog, OnlineSession
from backend.routers import (
    auth_router, dashboard_router, sync_router,
    medicines_router, suppliers_router, sales_router,
    prescriptions_router, settings_router, inventory_router,
    feedback_router, web_router, messaging_router, update_router,
)
from backend.routers import updates_router
from backend.routers import mpesa_router
from backend.routers import payment_settings_router
from backend.routers import medicine_scan_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting up %s...", APP_NAME)
    await init_db()
    log.info("%s is ready -- listening on port %d", APP_NAME, PORT)
    yield
    log.info("Shutting down %s...", APP_NAME)


app = FastAPI(
    title=APP_NAME,
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request logging middleware ──────────────────────────────────────────
@app.middleware("http")
async def log_requests(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") or path in ("/health", "/"):
        try:
            async with async_session() as db:
                from sqlalchemy import select
                ip = request.client.host if request.client else "unknown"
                result = await db.execute(
                    select(OnlineSession).where(OnlineSession.ip_address == ip)
                )
                session = result.scalar_one_or_none()
                if session:
                    session.last_ping = datetime.now(timezone.utc)
                else:
                    session = OnlineSession(
                        ip_address=ip,
                        user_agent=request.headers.get("user-agent", ""),
                        path=path,
                    )
                    db.add(session)
                await db.commit()
        except Exception as e:
            log.warning("Tracking error: %s", e)
    response = await call_next(request)
    return response


# ── Routers ────────────────────────────────────────────────────────────
app.include_router(auth_router.router)
app.include_router(dashboard_router.router)
app.include_router(sync_router.router)
app.include_router(medicines_router.router)
app.include_router(suppliers_router.router)
app.include_router(sales_router.router)
app.include_router(prescriptions_router.router)
app.include_router(settings_router.router)
app.include_router(inventory_router.router)
app.include_router(feedback_router.router)
app.include_router(updates_router.router)
app.include_router(mpesa_router.router)
app.include_router(payment_settings_router.router)
app.include_router(medicine_scan_router.router)
app.include_router(web_router.router)
app.include_router(messaging_router.router)
app.include_router(update_router.router)


# ── Mount update bundles as static files ──────────────────────────────
updates_dir = os.path.join(os.path.dirname(__file__), "updates")
if not os.path.isdir(updates_dir):
    os.makedirs(updates_dir, exist_ok=True)
    log.info("Created updates directory at %s", updates_dir)
app.mount("/updates/bundles", StaticFiles(directory=updates_dir), name="update_bundles")
log.info("Update bundles directory mounted at /updates/bundles")


# ── Server log helper ──────────────────────────────────────────────────
@app.middleware("http")
async def server_logger(request: Request, call_next):
    path = request.url.path
    if path.startswith(("/api/", "/health", "/feedback")):
        ip = request.client.host if request.client else "unknown"
        level = "INFO"
        msg = f"{request.method} {path}"
        try:
            async with async_session() as db:
                log_entry = ServerLog(level=level, message=msg, ip_address=ip, path=path)
                db.add(log_entry)
                await db.commit()
        except Exception:
            pass
    response = await call_next(request)
    return response
