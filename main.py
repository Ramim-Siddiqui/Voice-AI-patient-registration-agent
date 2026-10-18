"""Deployment entrypoint.

Vercel's Python runtime (and ``uvicorn main:app``) look for a module-level
``app``; the real application lives in ``app/main.py``.
"""

from app.main import app  # noqa: F401
