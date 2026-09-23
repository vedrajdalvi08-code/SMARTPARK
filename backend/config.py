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
    # SUPABASE_DB_URL is preferred, while DATABASE_URL is supported by
    # Render and other hosting platforms.
    url = (os.getenv("SUPABASE_DB_URL") or os.getenv("DATABASE_URL") or "").strip()

    if not url:
        # Development-only local SQLite fallback. Production must use Supabase.
        if os.getenv("FLASK_ENV", "development").lower() == "production":
            raise RuntimeError(
                "SUPABASE_DB_URL or DATABASE_URL is required in production."
            )
        return "sqlite:///smartpark_demo.db"

    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]

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
    SECRET_KEY = os.getenv(
        "FLASK_SECRET_KEY",
        os.getenv("SECRET_KEY", "change-this-flask-secret-key")
    )

    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "CHANGE_ME")

    # Supabase uses PostgreSQL underneath.
    DATABASE_URL = _database_url()
    SQLALCHEMY_DATABASE_URI = DATABASE_URL

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }

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

    IOT_AUTH_TOKEN = os.getenv("IOT_AUTH_TOKEN", "CHANGE_ME_IOT_TOKEN")
    PORT = int(os.getenv("PORT", 5000))
    DEBUG = os.getenv("FLASK_DEBUG", "1") == "1"
