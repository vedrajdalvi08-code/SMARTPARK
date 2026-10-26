"""
SMARTPARK - Configuration Module
Supabase/PostgreSQL production configuration.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path)


def _database_url():
    # An explicitly supplied DATABASE_URL takes precedence for local/test
    # development. Production still requires a PostgreSQL URL and can use
    # SUPABASE_DB_URL when DATABASE_URL is not supplied.
    url = (os.getenv("DATABASE_URL") or os.getenv("SUPABASE_DB_URL") or "").strip()

    if not url:
        # Development-only local SQLite fallback. Production must use Supabase.
        if os.getenv("FLASK_ENV", "development").lower() == "production":
            raise RuntimeError(
                "SUPABASE_DB_URL or DATABASE_URL is required in production."
            )
        return "sqlite:///smartpark_demo.db"

    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]

    if url.startswith("sqlite://") and os.getenv("FLASK_ENV", "development").lower() != "production":
        return url

    if not url.startswith(("postgresql://", "postgresql+psycopg2://")):
        raise ValueError(
            "SUPABASE_DB_URL/DATABASE_URL must be a PostgreSQL URL from Supabase."
        )

    # Supabase requires SSL for database connections.
    if "sslmode=" not in url:
        separator = "&" if "?" in url else "?"
        url += separator + "sslmode=require"

    if url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]

    return url


class Config:
    SECRET_KEY = os.getenv("FLASK_SECRET_KEY") or os.getenv("SECRET_KEY")

    # Supabase project credentials are server-side configuration only.
    SUPABASE_URL = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
    SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

    # Supabase uses PostgreSQL underneath.
    DATABASE_URL = _database_url()
    SQLALCHEMY_DATABASE_URI = DATABASE_URL
    DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "1"))
    DB_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "0"))
    DB_POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "30"))
    if DB_POOL_SIZE < 1 or DB_MAX_OVERFLOW < 0 or DB_POOL_TIMEOUT < 1:
        raise ValueError(
            "DB_POOL_SIZE must be at least 1; DB_MAX_OVERFLOW must be non-negative; "
            "DB_POOL_TIMEOUT must be at least 1 second."
        )

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
        "pool_size": DB_POOL_SIZE,
        "max_overflow": DB_MAX_OVERFLOW,
        "pool_timeout": DB_POOL_TIMEOUT,
    }
    AUTO_CREATE_SCHEMA = os.getenv("AUTO_CREATE_SCHEMA", "0" if os.getenv("FLASK_ENV", "development").lower() == "production" else "1") == "1"

    RATES = {
        "TWO_WHEELER": {
            "base": float(os.getenv("RATE_TWO_WHEELER_BASE", 10.0)),
            "hourly": float(os.getenv("RATE_TWO_WHEELER_HOURLY", 10.0))
        },
        "COMPACT": {
            "base": float(os.getenv("RATE_COMPACT_BASE", 20.0)),
            "hourly": float(os.getenv("RATE_COMPACT_HOURLY", 20.0))
        },
        "SUV": {
            "base": float(os.getenv("RATE_SUV_BASE", 30.0)),
            "hourly": float(os.getenv("RATE_SUV_HOURLY", 30.0))
        },
        "EV": {
            "base": float(os.getenv("RATE_EV_BASE", 25.0)),
            "hourly": float(os.getenv("RATE_EV_HOURLY", 25.0))
        }
    }

    PEAK_HOURS = [(9, 11), (17, 20)]
    PEAK_MULTIPLIER = 1.25

    IOT_AUTH_TOKEN = os.getenv("IOT_AUTH_TOKEN")
    PORT = int(os.getenv("PORT", 5000))
    DEBUG = os.getenv("FLASK_DEBUG", "0") == "1"
    FACILITY_LATITUDE = os.getenv("FACILITY_LATITUDE")
    FACILITY_LONGITUDE = os.getenv("FACILITY_LONGITUDE")
    MAP_TILE_URL = os.getenv("MAP_TILE_URL", "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png")

    @classmethod
    def validate(cls):
        production = os.getenv("FLASK_ENV", "development").lower() == "production"
        required = {"FLASK_SECRET_KEY": cls.SECRET_KEY, "ADMIN_PASSWORD": cls.ADMIN_PASSWORD}
        if production:
            missing = [name for name, value in required.items() if not value]
            weak = [name for name, value in required.items() if value and (len(value) < 24 or "CHANGE_ME" in value)]
            if missing or weak:
                raise RuntimeError(f"Missing or weak production secrets: {', '.join(missing + weak)}")
        else:
            cls.SECRET_KEY = cls.SECRET_KEY or "local-development-only-secret"
            cls.ADMIN_PASSWORD = cls.ADMIN_PASSWORD or "local-development-password"
            cls.IOT_AUTH_TOKEN = cls.IOT_AUTH_TOKEN or ""
