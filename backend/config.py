import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("pharmacy")

import os
from dotenv import load_dotenv

load_dotenv()

DB_USER = os.getenv("DB_USER", "avnadmin")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_HOST = os.getenv("DB_HOST", "agriconnect-oyoookoth42-489d.h.aivencloud.com")
DB_PORT = os.getenv("DB_PORT", "25592")
DB_NAME = os.getenv("DB_NAME", "pharmacy-db")
DB_SSLMODE = os.getenv("DB_SSLMODE", "require")

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "480"))
APP_NAME = os.getenv("APP_NAME", "Kevin Odongo Pharmacy API")
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*")
ADMIN_DEFAULT_PASSWORD = os.getenv("ADMIN_DEFAULT_PASSWORD", "admin123")
WEBPAGE_API_PIN = os.getenv("WEBPAGE_API_PIN", "9198")
PORT = int(os.getenv("PORT", "8000"))


def build_db_url(driver: str) -> str:
    pw = f":{DB_PASSWORD}" if DB_PASSWORD else ""
    ssl = f"?ssl={DB_SSLMODE}" if DB_SSLMODE else ""
    return f"postgresql+{driver}://{DB_USER}{pw}@{DB_HOST}:{DB_PORT}/{DB_NAME}{ssl}"


DATABASE_URL = os.getenv("DATABASE_URL") or build_db_url("asyncpg")
DATABASE_URL_SYNC = os.getenv("DATABASE_URL_SYNC") or build_db_url("psycopg2")
