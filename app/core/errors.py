# app/core/errors.py
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

log = logging.getLogger("app.errors")


# -----------------------------
# Trace / request id helpers
# -----------------------------
def _ensure_trace_id(request: Request) -> str:
    """
    Return a stable trace_id for this request.
    Prefer a value already set on request.state, then common headers,
    and finally generate a new one (and store it on request.state).
    """
    # Already set by middleware?
    for attr in ("trace_id", "request_id"):
        val = getattr(getattr(request, "state", object()), attr, None)
        if val:
            return str(val)

    # Common inbound correlation headers (case-insensitive via .get)
    for h in ("x-request-id", "x-correlation-id", "x-trace-id", "X-Request-ID"):
        v = request.headers.get(h)
        if v:
            try:
                request.state.trace_id = v
            except Exception:
                pass
            return v

    # Fallback
    new_id = uuid.uuid4().hex
    try:
        request.state.trace_id = new_id
    except Exception:
        pass
    return new_id


def _payload(
    *,
    message: str,
    typ: str,
    status: int,
    trace_id: str,
    details: Optional[Any] = None,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "ok": False,
        "error": {
            "type": typ,
            "message": message,
            "status": status,
            "trace_id": trace_id,
        },
    }
    if details is not None:
        body["error"]["details"] = details
    return body


# -----------------------------
# Install / register handlers
# -----------------------------
def register_exception_handlers(app: FastAPI) -> None:
    """
    Registers consistent JSON error handlers.
    Also ensures X-Request-ID header is present on error responses.
    """

    @app.exception_handler(HTTPException)
    async def http_exc_handler(request: Request, exc: HTTPException):
        trace_id = _ensure_trace_id(request)
        status_code = int(exc.status_code)
        # detail can be str, dict, or other; keep a safe message
        message = exc.detail if isinstance(exc.detail, str) else "HTTP error"
        details = exc.detail if isinstance(exc.detail, dict) else None

        headers = dict(exc.headers or {})
        headers["X-Request-ID"] = trace_id

        # Log level by class
        level = logging.ERROR if status_code >= 500 else logging.WARNING
        log.log(
            level,
            "HTTPException %s %s -> %s | trace_id=%s | detail=%r",
            request.method,
            request.url.path,
            status_code,
            trace_id,
            exc.detail,
        )

        return JSONResponse(
            status_code=status_code,
            headers=headers,
            content=_payload(
                message=message,
                typ="http_error",
                status=status_code,
                trace_id=trace_id,
                details=details,
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exc_handler(request: Request, exc: RequestValidationError):
        trace_id = _ensure_trace_id(request)
        errors = exc.errors()
        log.warning(
            "ValidationError %s %s -> 422 | trace_id=%s | errors=%s",
            request.method,
            request.url.path,
            trace_id,
            errors,
        )
        return JSONResponse(
            status_code=422,
            headers={"X-Request-ID": trace_id},
            content=_payload(
                message="Validation failed.",
                typ="validation_error",
                status=422,
                trace_id=trace_id,
                details=errors,
            ),
        )

    @app.exception_handler(Exception)
    async def unhandled_exc_handler(request: Request, exc: Exception):
        trace_id = _ensure_trace_id(request)
        # Full traceback to server logs; generic message to client
        log.exception(
            "Unhandled exception %s %s -> 500 | trace_id=%s",
            request.method,
            request.url.path,
            trace_id,
        )
        return JSONResponse(
            status_code=500,
            headers={"X-Request-ID": trace_id},
            content=_payload(
                message="Internal server error.",
                typ="internal_error",
                status=500,
                trace_id=trace_id,
            ),
        )


# Optional alias for alternative import name
install_error_handlers = register_exception_handlers