from fastapi import FastAPI

from app.observability.correlation import CORRELATION_HEADER
from tests.helpers import logged_in


async def test_liveness_and_readiness(app: FastAPI) -> None:
    async with logged_in(app, None) as client:
        assert (await client.get("/healthz")).json() == {"status": "ok"}

        ready = await client.get("/readyz")
        assert ready.status_code == 200
        assert CORRELATION_HEADER in ready.headers
