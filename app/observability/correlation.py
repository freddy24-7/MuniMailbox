import time
import uuid

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

CORRELATION_HEADER = "X-Correlation-ID"

logger = structlog.get_logger(__name__)


def _valid_or_new(candidate: str | None) -> str:
    """Accept a caller-supplied ID only if it is a UUID; the header is untrusted input
    and ends up in every log line."""
    if candidate is not None:
        try:
            return str(uuid.UUID(candidate))
        except ValueError:
            pass
    return str(uuid.uuid4())


class CorrelationIdMiddleware:
    """Binds one correlation ID per HTTP request to the logging context and echoes it
    in the response. The pipeline binds the email's own correlation ID the same way."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        correlation_id = _valid_or_new(Headers(scope=scope).get(CORRELATION_HEADER))
        status_code = 500
        started = time.perf_counter()

        async def send_with_header(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)[CORRELATION_HEADER] = correlation_id
            await send(message)

        with structlog.contextvars.bound_contextvars(correlation_id=correlation_id):
            try:
                await self.app(scope, receive, send_with_header)
            finally:
                logger.info(
                    "http.request",
                    method=scope["method"],
                    path=scope["path"],
                    status=status_code,
                    duration_ms=round((time.perf_counter() - started) * 1000, 1),
                )
