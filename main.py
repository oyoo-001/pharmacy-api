import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.config import APP_NAME, CORS_ORIGINS, PORT, log
from backend.database import init_db
from backend.routers import auth_router, dashboard_router, sync_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting up %s…", APP_NAME)
    await init_db()
    log.info("%s is ready — listening on port %d", APP_NAME, PORT)
    yield
    log.info("Shutting down %s…", APP_NAME)


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

app.include_router(auth_router.router)
app.include_router(dashboard_router.router)
app.include_router(sync_router.router)


@app.get("/")
async def root():
    return {"message": f"{APP_NAME} is running"}


@app.get("/health")
async def health():
    return {"status": "ok"}
