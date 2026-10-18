"""Consistent JSON envelope ``{"data": ..., "error": ...}`` and error types.

Status code policy
------------------
* 200 / 201  success (201 only for creation)
* 400        malformed request: bad JSON, bad UUID in the path, bad query parameter
* 401        missing/invalid API key (only when ``API_KEY`` is configured)
* 404        unknown route or unknown / soft-deleted patient
* 422        well-formed request whose fields fail validation
* 500        unexpected server error (details are logged, never leaked)
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.schemas.patient import format_validation_errors

log = logging.getLogger(__name__)


def ok(data: Any, *, status_code: int = 200, meta: dict[str, Any] | None = None) -> JSONResponse:
    body: dict[str, Any] = {"data": data, "error": None}
    if meta is not None:
        body["meta"] = meta
    return JSONResponse(body, status_code=status_code)


def fail(
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, str]] | None = None,
) -> JSONResponse:
    error: dict[str, Any] = {"code": code, "message": message}
    if details:
        error["details"] = details
    return JSONResponse({"data": None, "error": error}, status_code=status_code)


class ApiError(Exception):
    status_code = 500
    code = "INTERNAL_ERROR"

    def __init__(self, message: str, details: list[dict[str, str]] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details


class BadRequestError(ApiError):
    status_code = 400
    code = "BAD_REQUEST"


class UnauthorizedError(ApiError):
    status_code = 401
    code = "UNAUTHORIZED"


class NotFoundError(ApiError):
    status_code = 404
    code = "NOT_FOUND"


class UnprocessableError(ApiError):
    status_code = 422
    code = "VALIDATION_ERROR"


_HTTP_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    422: "VALIDATION_ERROR",
}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return fail(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _request_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = exc.errors()
        # Malformed/missing JSON body or bad query/path parameters -> 400.
        if any(
            e.get("type") in ("json_invalid", "model_attributes_type")
            or (e.get("loc") and e["loc"][0] in ("query", "path"))
            or (e.get("loc") == ("body",) and e.get("type") == "missing")
            for e in errors
        ):
            details = [
                {
                    "field": ".".join(str(p) for p in e.get("loc", ()) if p not in ("body",)) or "body",
                    "message": e.get("msg", "is invalid"),
                }
                for e in errors
            ]
            return fail(400, "BAD_REQUEST", "Malformed request", details)
        details = _details_from_errors(errors)
        return fail(422, "VALIDATION_ERROR", "Validation failed", details)

    @app.exception_handler(ValidationError)
    async def _pydantic_validation(_: Request, exc: ValidationError) -> JSONResponse:
        return fail(422, "VALIDATION_ERROR", "Validation failed", format_validation_errors(exc))

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
        message = exc.detail if isinstance(exc.detail, str) else "Request failed"
        return fail(exc.status_code, code, message)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return fail(500, "INTERNAL_ERROR", "An unexpected error occurred")


def _details_from_errors(errors: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Reuse the shared formatter by faking a pydantic error list."""

    class _Shim:
        def __init__(self, errs: list[dict[str, Any]]):
            self._errs = errs

        def errors(self) -> list[dict[str, Any]]:
            return self._errs

    return format_validation_errors(_Shim(errors))  # type: ignore[arg-type]
