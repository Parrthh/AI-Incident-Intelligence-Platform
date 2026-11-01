"""Request ID and access logging middleware.

Every request gets an ID. If the caller sends ``X-Request-ID`` (a load balancer
or another service that already started a trace), we reuse it so one ID follows
the request across systems. Otherwise we generate one. The ID is:
- bound to the logging context, so every log line for this request carries it
- returned in the response header, so a user reporting a problem can quote it

This is written as plain ASGI middleware rather than Starlette's
``BaseHTTPMiddleware``. The plain form doesn't buffer responses and doesn't break
contextvars, at the cost of a little more code.
"""

import re
import time
import uuid

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "x-request-id"

# Accept caller-supplied IDs only if they look like IDs. Anything else (very long
# values, spaces, control characters) is replaced, so a client can't inject junk
# into our logs.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

# Health checks are polled constantly by the platform; logging each one is noise.
_QUIET_PATHS = frozenset({"/healthz", "/readyz"})

logger = structlog.get_logger("iip.access")


def _incoming_request_id(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name == REQUEST_ID_HEADER.encode():
            candidate = value.decode("latin-1")
            return candidate if _VALID_REQUEST_ID.match(candidate) else None
    return None


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope) or uuid.uuid4().hex
        path: str = scope.get("path", "")
        method: str = scope.get("method", "")
        status_code = 500
        start = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
            await send(message)

        # bound_contextvars restores the previous context when the block exits,
        # so IDs never leak from one request to the next.
        with structlog.contextvars.bound_contextvars(request_id=request_id):
            try:
                await self.app(scope, receive, send_wrapper)
            finally:
                duration_ms = round((time.perf_counter() - start) * 1000, 2)
                if path not in _QUIET_PATHS or status_code >= 400:
                    logger.info(
                        "request",
                        method=method,
                        path=path,
                        status=status_code,
                        duration_ms=duration_ms,
                    )
