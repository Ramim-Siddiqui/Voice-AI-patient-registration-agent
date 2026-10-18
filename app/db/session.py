"""Database engine, session factory and lazy initialisation.

* Local development and tests use SQLite (zero setup; the file lives in ``./data``).
* Production uses PostgreSQL (Neon free tier) through the very same models.

Initialisation (``CREATE TABLE IF NOT EXISTS`` + optional demo seed) happens
lazily on the first request of each process instead of in an ASGI lifespan
hook, because serverless hosts do not reliably run lifespan events.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.db.models import Base, Patient

log = logging.getLogger(__name__)

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_initialized = False
_lock = threading.RLock()  # re-entrant: init_db() -> get_engine() both take it


def _build_engine() -> Engine:
    settings = get_settings()
    url = settings.sqlalchemy_url

    if settings.is_sqlite:
        in_memory = ":memory:" in url or url.rstrip("/") in ("sqlite:", "sqlite+pysqlite:")
        kwargs: dict = {"connect_args": {"check_same_thread": False}}
        if in_memory:
            # One shared connection so every session sees the same in-memory DB.
            kwargs["poolclass"] = StaticPool
        else:
            db_path = url.split("///", 1)[-1]
            Path(db_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, **kwargs)

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - trivial
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        return engine

    # PostgreSQL (Neon).  Small pool + pre-ping suits serverless instances where
    # a connection may have been dropped by the pooler while the instance idled.
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=3,
        pool_recycle=300,
        # Neon's free tier scales to zero; the first connection after idle can
        # take a few seconds while the compute resumes.
        connect_args={"connect_timeout": 10},
    )


def get_engine() -> Engine:
    global _engine, _session_factory
    if _engine is None:
        with _lock:
            if _engine is None:
                _engine = _build_engine()
                _session_factory = sessionmaker(
                    bind=_engine, autoflush=False, expire_on_commit=False
                )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    get_engine()
    assert _session_factory is not None
    return _session_factory


def init_db(*, seed: bool | None = None) -> None:
    """Create tables if needed and (optionally) insert demo rows. Idempotent."""
    global _initialized
    if _initialized:
        return
    with _lock:
        if _initialized:
            return
        settings = get_settings()
        engine = get_engine()
        try:
            Base.metadata.create_all(engine)
        except (ProgrammingError, OperationalError, IntegrityError) as exc:
            # Two serverless instances may cold-start on a fresh database at the
            # same moment; the loser of the CREATE TABLE race just re-checks.
            log.warning("schema creation raced with another instance (%s); re-checking", exc.__class__.__name__)
            Base.metadata.create_all(engine, checkfirst=True)
        if seed if seed is not None else settings.seed_demo_data:
            _seed_demo_patients()
        _initialized = True
        log.info("database initialised (%s)", "sqlite" if settings.is_sqlite else "postgresql")


def _seed_demo_patients() -> None:
    """Insert two demonstration patients when the table is empty."""
    from app.db.seed import DEMO_PATIENTS  # local import to avoid a cycle

    factory = get_session_factory()
    with factory() as session:
        if not get_settings().is_sqlite:
            # Serialise concurrent cold starts so the demo rows are inserted once.
            # Transaction-scoped, so it is safe behind PgBouncer (Neon's pooler).
            session.execute(text("SELECT pg_advisory_xact_lock(814023)"))
        if session.scalar(select(Patient.patient_id).limit(1)) is not None:
            session.rollback()
            return
        for row in DEMO_PATIENTS:
            session.add(Patient(**row))
        session.commit()
        log.info("seeded %d demo patients", len(DEMO_PATIENTS))


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: one session per request."""
    if get_settings().auto_init_db:
        init_db()
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def reset_engine() -> None:
    """Dispose the engine and forget initialisation state (used by the tests)."""
    global _engine, _session_factory, _initialized
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _session_factory = None
        _initialized = False
