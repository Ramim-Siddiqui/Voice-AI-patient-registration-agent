"""FastAPI application factory.

Layers (see README "Architecture"):

    telephony / voice  ->  app.voice   (Vapi webhook + tool handlers + prompt)
    REST API           ->  app.api     (patients, calls, health, envelope)
    business logic     ->  app.services
    persistence        ->  app.db      (SQLAlchemy models, SQLite or PostgreSQL)
    dashboard          ->  app.dashboard (static HTML that consumes the API)
"""

from __future__ import annotations

import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api import calls, health, patients
from app.api.envelope import register_exception_handlers
from app.config import get_settings
from app.dashboard.router import router as dashboard_router
from app.logging_utils import configure_logging
from app.voice.webhook import router as vapi_router

log = logging.getLogger("app.http")

DESCRIPTION = """
REST API behind the **voice patient-registration agent**.

* Every response uses the envelope `{"data": ..., "error": null}` /
  `{"data": null, "error": {"code", "message", "details?"}}`.
* `POST /vapi/webhook` is the endpoint the voice platform (Vapi) calls for
  tool calls and end-of-call reports.
"""


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description=DESCRIPTION,
        docs_url="/docs",
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)

    @app.middleware("http")
    async def access_log(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000
        log.info(
            "%s %s -> %s (%.0f ms)",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
        return response

    app.include_router(health.router)
    app.include_router(patients.router)
    app.include_router(calls.router)
    app.include_router(vapi_router)
    app.include_router(dashboard_router)
    return app


app = create_app()
