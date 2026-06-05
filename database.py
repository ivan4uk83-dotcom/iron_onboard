"""
database.py — SQLAlchemy engine + session factory + declarative Base.

Supports:
  • PostgreSQL via DATABASE_URL in .env  (Supabase / Render production)
  • SQLite fallback when DATABASE_URL is empty  (local development / tests)
"""

import os
from typing import Generator

from dotenv import load_dotenv
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

load_dotenv()

# ── 1. Resolve DATABASE_URL ───────────────────────────────────────────────────

_raw_url: str = os.getenv("DATABASE_URL", "")

if not _raw_url:
    # SQLite fallback — file created in the project root
    DATABASE_URL = "sqlite:///./fitness_local.db"
    print("[DB] DATABASE_URL not set — using SQLite fallback: fitness_local.db")
else:
    # Supabase / older Heroku-style URLs start with "postgres://"
    # SQLAlchemy 2.x requires "postgresql+psycopg2://"
    DATABASE_URL = _raw_url.replace("postgres://", "postgresql+psycopg2://", 1)
    if not DATABASE_URL.startswith("postgresql+psycopg2://"):
        # Already correct scheme (e.g. "postgresql://…") — just ensure driver suffix
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)
    # Supabase / Render require SSL — append if not already present
    if "sslmode" not in DATABASE_URL:
        DATABASE_URL += ("&" if "?" in DATABASE_URL else "?") + "sslmode=require"
    print("[DB] Using PostgreSQL database.")

# ── 2. Engine ─────────────────────────────────────────────────────────────────

_is_sqlite = DATABASE_URL.startswith("sqlite")

_engine_kwargs: dict = {}

if _is_sqlite:
    # SQLite requires this flag when using a single connection across threads
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    # Connection pool tuning for Supabase (PgBouncer-friendly)
    _engine_kwargs["pool_pre_ping"] = True      # drops stale connections
    _engine_kwargs["pool_size"] = 5
    _engine_kwargs["max_overflow"] = 10

engine = create_engine(DATABASE_URL, **_engine_kwargs)

# Enable WAL mode for SQLite — better concurrent read performance
if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()

# ── 3. Session Factory ────────────────────────────────────────────────────────

SessionLocal: sessionmaker[Session] = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
)

# ── 4. Declarative Base ───────────────────────────────────────────────────────

class Base(DeclarativeBase):
    """Shared declarative base — all ORM models inherit from this."""
    pass

# ── 5. Dependency (FastAPI / pytest) ─────────────────────────────────────────

def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency that yields a DB session and ensures it is closed
    after the request — even if an exception occurs.

    Usage in a route:
        @router.get("/")
        def my_route(db: Session = Depends(get_db)):
            ...
    """
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()
