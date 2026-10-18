"""Application settings.

Every value is read from environment variables (a local ``.env`` file is also
honoured).  Nothing secret is hard-coded anywhere in the code base.

See ``.env.example`` for the full list with explanations.
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- general -----------------------------------------------------------
    app_name: str = "Voice Patient Registration"
    environment: str = Field(default="development", description="development | production | test")
    log_level: str = "INFO"

    # --- database ----------------------------------------------------------
    # SQLite file for local development; a PostgreSQL URL (Neon) in production.
    database_url: str = "sqlite:///./data/patients.db"
    # Create tables lazily on first use (safe: idempotent) and seed demo rows.
    auto_init_db: bool = True
    seed_demo_data: bool = True

    # --- security ----------------------------------------------------------
    # Optional shared key.  When set, POST/PUT/DELETE on /patients require the
    # ``X-API-Key`` header (or ``Authorization: Bearer``).  Left empty for the
    # review period so the API can be exercised freely.
    api_key: str | None = None
    # Optional shared secret.  When set, every Vapi webhook must carry it in the
    # ``x-vapi-secret`` header.  scripts/vapi_setup.py sends it as a custom
    # ``server.headers`` entry on the assistant and its tools (Vapi's current API
    # has no ``server.secret`` field).
    vapi_webhook_secret: str | None = None

    # --- voice agent persona (used in the system prompt) -------------------
    clinic_name: str = "Riverside Family Clinic"
    agent_name: str = "Riley"
    # Mirrors scripts/vapi_setup.py: when true the transcriber is multilingual and
    # the prompt is allowed to switch to Spanish (only affects /vapi/assistant-config here).
    vapi_multilingual: bool = False

    # --- public URL of this deployment (used by scripts/vapi_setup.py) -----
    public_base_url: str | None = None

    @field_validator("api_key", "vapi_webhook_secret", "public_base_url", mode="before")
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        """Treat empty strings in env files as "not set"."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def sqlalchemy_url(self) -> str:
        """Return the DATABASE_URL in the dialect+driver form SQLAlchemy expects.

        Neon/Heroku style URLs use the generic ``postgres://`` or
        ``postgresql://`` schemes; we route them to the psycopg 3 driver.
        """
        url = self.database_url.strip()
        if url.startswith("postgres://"):
            return "postgresql+psycopg://" + url[len("postgres://"):]
        if url.startswith("postgresql://"):
            return "postgresql+psycopg://" + url[len("postgresql://"):]
        return url

    @property
    def is_sqlite(self) -> bool:
        return self.sqlalchemy_url.startswith("sqlite")

    @property
    def base_url(self) -> str | None:
        """Public URL of this deployment: PUBLIC_BASE_URL, else Vercel's own env vars."""
        if self.public_base_url:
            return self.public_base_url.rstrip("/")
        for var in ("VERCEL_PROJECT_PRODUCTION_URL", "VERCEL_URL"):
            host = os.getenv(var, "").strip()
            if host:
                return f"https://{host}"
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
