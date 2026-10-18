"""Serves the single-page dashboard (plain HTML + JS that consumes the REST API)."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.config import get_settings
from app.dashboard.page import render_dashboard

router = APIRouter(tags=["dashboard"])


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard() -> HTMLResponse:
    settings = get_settings()
    return HTMLResponse(render_dashboard(clinic_name=settings.clinic_name))
