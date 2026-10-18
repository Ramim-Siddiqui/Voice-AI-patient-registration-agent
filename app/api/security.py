"""Optional API-key protection for mutating endpoints.

When ``API_KEY`` is unset (the default for the review period) the dependency is
a no-op, so the API is fully open.  When set, callers must send either
``X-API-Key: <key>`` or ``Authorization: Bearer <key>``.
"""

from __future__ import annotations

import secrets

from fastapi import Request

from app.api.envelope import UnauthorizedError
from app.config import get_settings


def require_api_key(request: Request) -> None:
    expected = get_settings().api_key
    if not expected:
        return
    provided = request.headers.get("x-api-key")
    if not provided:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            provided = auth[7:].strip()
    # Compare bytes so a non-ASCII header value cannot raise (-> 500) instead of 401.
    if not provided or not secrets.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        raise UnauthorizedError("a valid API key is required (X-API-Key header)")
