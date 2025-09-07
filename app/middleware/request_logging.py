# app/middleware/request_logging.py
from __future__ import annotations

import logging
import time
import uuid
from typing import Iterable, Tuple

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("app.request")


DEFAULT_IGNORED_PREFIXES: Tuple[str, ...] = (
    "/api/healthz",
    "/api/readyz",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/favicon.ico",
)


def _client_ip(request: Request) -> str:
    # Honor common proxy header if present (do NOT trust blindly in high-security contexts)
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # take the first IP in the list
        return xff.split(",")[0].strip()
    client = request.client
    return client.host if client else "unknown"


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Logs each request with a stable trace_id and duration.
    Adds X-Request-ID header to every response.

    Skips common framework/health endpoints.
    """

    def __init__(self, app, ignored_prefixes: Iterable[str] = DEFAULT_IGNORED_PREFIXES):
        super().__init__(app)
        self.ignored_prefixes = tuple(ignored_prefixes)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        method = request.method.upper()

        # Generate / propagate trace_id early
        trace_id = getattr(request.state, "trace_id", None) or request.headers.get("x-request-id") or uuid.uuid4().hex
        try:
            request.state.trace_id = trace_id
        except Exception:
            # request.state may be immutable in rare cases; safe to ignore
            pass

        # Skip logging for OPTIONS and ignored paths (still set X-Request-ID)
        skip = method == "OPTIONS" or any(path.startswith(p) for p in self.ignored_prefixes)

        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # Log crash timing, then let global error handlers respond
            if not skip:
                duration_ms = int((time.perf_counter() - start) * 1000)
                logger.exception(
                    "request CRASH %s %s ip=%s ua=%r dur_ms=%s trace_id=%s",
                    method,
                    path,
                    _client_ip(request),
                    request.headers.get("user-agent", "-"),
                    duration_ms,
                    trace_id,
                )
            raise

        # Always attach the trace header
        try:
            response.headers["X-Request-ID"] = trace_id
        except Exception:
            pass

        if skip:
            return response

        duration_ms = int((time.perf_counter() - start) * 1000)
        status = getattr(response, "status_code", 0)
        ua = request.headers.get("user-agent", "-")
        ip = _client_ip(request)
        length = response.headers.get("content-length", "-")

        # Choose log level by status class
        if status >= 500:
            level = logging.ERROR
        elif status >= 400:
            level = logging.WARNING
        else:
            level = logging.INFO

        logger.log(
            level,
            "request %s %s -> %s len=%s ip=%s ua=%r dur_ms=%s trace_id=%s",
            method,
            path,
            status,
            length,
            ip,
            ua,
            duration_ms,
            trace_id,
        )
        return response