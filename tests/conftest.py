"""Shared pytest fixtures.

Every test gets a brand-new in-memory SQLite database and a fresh FastAPI app,
so tests are fully isolated and need no external services.
"""

from __future__ import annotations

import os
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

TEST_ENV = {
    "DATABASE_URL": "sqlite+pysqlite:///:memory:",
    "AUTO_INIT_DB": "true",
    "SEED_DEMO_DATA": "false",
    "API_KEY": "",
    "VAPI_WEBHOOK_SECRET": "",
    "ENVIRONMENT": "test",
    "LOG_LEVEL": "WARNING",
}
for _key, _value in TEST_ENV.items():
    os.environ[_key] = _value

from app.config import get_settings  # noqa: E402  (env must be set first)
from app.db.session import reset_engine  # noqa: E402


@pytest.fixture
def make_client() -> Callable[..., TestClient]:
    """Factory fixture: ``make_client(API_KEY="secret")`` overrides env vars."""
    clients: list[TestClient] = []

    def _make(**env_overrides: str) -> TestClient:
        for key, value in env_overrides.items():
            os.environ[key] = value
        get_settings.cache_clear()
        reset_engine()
        from app.main import create_app

        client = TestClient(create_app())
        client.__enter__()
        clients.append(client)
        return client

    yield _make

    for client in clients:
        client.__exit__(None, None, None)
    for key, value in TEST_ENV.items():
        os.environ[key] = value
    get_settings.cache_clear()
    reset_engine()


@pytest.fixture
def client(make_client) -> TestClient:
    return make_client()


VALID_PATIENT = {
    "first_name": "jane",
    "last_name": "O'Connor-Smith",
    "date_of_birth": "03/15/1985",
    "sex": "female",
    "phone_number": "+1 (555) 123-4567",
    "email": "Jane.OConnor@Example.com",
    "address_line_1": "123 Maple Street",
    "city": "Springfield",
    "state": "Illinois",
    "zip_code": "62704 1234",
}


@pytest.fixture
def valid_patient() -> dict:
    return dict(VALID_PATIENT)
