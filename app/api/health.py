"""Health / readiness endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.envelope import ok
from app.config import get_settings
from app.db.session import get_db

router = APIRouter(tags=["health"])


@router.get("/healthz", summary="Liveness only (no database access; safe for uptime pingers)")
def healthz():
    # Deliberately touches nothing: pinging /health every few minutes would keep
    # a scale-to-zero database (Neon free tier) awake and burn its compute quota.
    return ok({"status": "ok"})


@router.get("/health", summary="Readiness: liveness + database connectivity check")
def health(db: Session = Depends(get_db)):
    settings = get_settings()
    db.execute(text("SELECT 1"))
    return ok(
        {
            "status": "ok",
            "service": settings.app_name,
            "environment": settings.environment,
            "database": "sqlite" if settings.is_sqlite else "postgresql",
        }
    )
