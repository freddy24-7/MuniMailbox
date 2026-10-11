import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.observability.metrics import refresh_gauges

# Public on purpose: used by Docker, systemd and the deploy health check. No data is returned.
router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: the process is up."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(session: Annotated[AsyncSession, Depends(get_session)]) -> dict[str, str]:
    """Readiness: the database answers."""
    await session.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.get("/metrics")
async def metrics(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> Response:
    """Prometheus scrapes this (NFR-1). Numbers only: no email content, no identifiers.

    Prometheus cannot log in, so the endpoint has no user. With METRICS_TOKEN set it
    requires that token as a bearer token; set it wherever the port is reachable by
    others than Prometheus.
    """
    token = request.app.state.services.settings.metrics_token
    if token is not None:
        given = request.headers.get("Authorization", "")
        if not secrets.compare_digest(given, f"Bearer {token.get_secret_value()}"):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED)
    await refresh_gauges(session)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
