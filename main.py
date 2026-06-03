"""
main.py — FastAPI application entry point for Iron Man Fitness SaaS.

Startup:
  • Reads DATABASE_URL from .env (PostgreSQL) or falls back to SQLite.
  • Creates all tables via SQLAlchemy metadata (dev/staging convenience).
  • For production migrations use Alembic: `alembic upgrade head`

Run locally:
    uvicorn main:app --reload --port 8000

Render deployment:
    Start command: uvicorn main:app --host 0.0.0.0 --port $PORT
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from database import Base, engine

# ── Import all models so SQLAlchemy registers them with Base.metadata ─────────
import models  # noqa: F401

# ── Routers ───────────────────────────────────────────────────────────────────
from routers import auth, workouts, onboarding

load_dotenv()

# ══════════════════════════════════════════════════════════════════════════════
# Lifespan (replaces deprecated @app.on_event)
# ══════════════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup: auto-create all tables that do not yet exist.

    NOTE: In production, prefer Alembic migrations instead of create_all
    to safely evolve the schema without data loss.
    """
    Base.metadata.create_all(bind=engine)
    print("[Startup] Database tables verified / created.")
    yield
    # ── Shutdown ──────────────────────────────────────────────────────────────
    print("[Shutdown] Application stopping.")


# ══════════════════════════════════════════════════════════════════════════════
# FastAPI Application
# ══════════════════════════════════════════════════════════════════════════════

app = FastAPI(
    title="Iron Man Fitness API",
    description=(
        "Multi-user fitness SaaS backend.\n\n"
        "Roles: **admin** · **coach** · **client**\n\n"
        "Features: onboarding, auto-weight progression, plateau detection, "
        "coach calendar, 1RM calculation & synergy weights."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────────────────────
# Restrict origins in production via the ALLOWED_ORIGINS env variable.
_raw_origins = os.getenv("ALLOWED_ORIGINS", "*")
_origins = [o.strip() for o in _raw_origins.split(",")] if _raw_origins != "*" else ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ══════════════════════════════════════════════════════════════════════════════
# Health / Root Routes
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/", tags=["Health"], summary="Root ping")
async def root():
    """Returns a quick alive signal — used by Render health checks."""
    return {"status": "ok", "service": "Iron Man Fitness API"}


@app.get("/health", tags=["Health"], summary="Detailed health check")
async def health_check():
    """
    Verifies the app is reachable.
    Extend this endpoint to add DB connectivity checks before go-live.
    """
    return {
        "status": "healthy",
        "database_url_set": bool(os.getenv("DATABASE_URL")),
        "environment": os.getenv("APP_ENV", "development"),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Routers
# ══════════════════════════════════════════════════════════════════════════════

app.include_router(auth.router)         # /auth/register  /auth/login  /auth/me
app.include_router(workouts.router)     # /workouts/first  /workouts/log  /workouts/records
app.include_router(onboarding.router)   # /onboarding/status  /onboarding/update  /onboarding/missed-workout

# ── Future routers (uncomment as you build them) ──────────────────────────────
# from routers import exercises, calendar
# app.include_router(exercises.router)  # /exercises
# app.include_router(calendar.router)   # /calendar
