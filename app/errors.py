"""Error model and exception handlers matching spec section A.7.3.

Every error is returned as:
    {"error": {"code": "...", "message": "...", "details": ...}}
"""
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)


class APIError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


# Convenience constructors for the codes defined in the spec.
def validation_error(message: str, details: Any = None) -> APIError:
    return APIError(400, "VALIDATION_ERROR", message, details)


def unsupported_media_type(ext: str, accepted: list[str]) -> APIError:
    return APIError(
        400,
        "UNSUPPORTED_MEDIA_TYPE",
        f"File type .{ext} is not supported. Accepted: {', '.join(accepted)}",
    )


def unauthorized() -> APIError:
    return APIError(401, "UNAUTHORIZED", "Authorization token missing or invalid")


def forbidden() -> APIError:
    return APIError(403, "FORBIDDEN", "Caller does not have permission for this operation")


def job_not_found(job_id: str) -> APIError:
    return APIError(404, "JOB_NOT_FOUND", f"No job found with id {job_id}")


def file_not_accessible(file_name: str) -> APIError:
    return APIError(404, "FILE_NOT_ACCESSIBLE", f"Unable to access {file_name} in temporary storage")


def payload_too_large(limit_mb: int) -> APIError:
    return APIError(413, "PAYLOAD_TOO_LARGE", f"Combined file size exceeds maximum allowed size of {limit_mb} MB")


def conversion_failed() -> APIError:
    return APIError(422, "CONVERSION_FAILED", "No files could be converted to PDF")


def service_unavailable() -> APIError:
    return APIError(503, "SERVICE_UNAVAILABLE", "ContractAI is temporarily unavailable. Try again shortly.")


def _body(code: str, message: str, details: Any = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(APIError)
    async def _api_error(_: Request, exc: APIError):
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        return JSONResponse(_body(exc.code, exc.message, exc.details), exc.status_code, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        errors = exc.errors()
        first = errors[0] if errors else {}
        loc = ".".join(str(p) for p in first.get("loc", []) if p not in ("body", "query", "path", "form"))
        msg = first.get("msg", "Invalid request")
        message = f"{loc}: {msg}" if loc else msg
        details = [
            {"field": ".".join(str(p) for p in e.get("loc", [])), "message": e.get("msg")} for e in errors
        ]
        return JSONResponse(_body("VALIDATION_ERROR", message, details), 400)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        return JSONResponse(_body(code, str(exc.detail)), exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        ts = datetime.now(timezone.utc).isoformat()
        log.exception("Unhandled error at %s", ts)
        return JSONResponse(
            _body(
                "INTERNAL_ERROR",
                "An unexpected error occurred. Contact support with request timestamp.",
                {"timestamp": ts},
            ),
            500,
        )
