from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.observability.correlation import CORRELATION_HEADER


def test_liveness_and_readiness(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        assert client.get("/healthz").json() == {"status": "ok"}

        ready = client.get("/readyz")
        assert ready.status_code == 200
        assert CORRELATION_HEADER in ready.headers
