import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from app.observability.correlation import CORRELATION_HEADER, CorrelationIdMiddleware


def _client() -> TestClient:
    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"pong": "ok"}

    return TestClient(app)


def test_generates_an_id_when_none_is_sent() -> None:
    response = _client().get("/ping")
    assert uuid.UUID(response.headers[CORRELATION_HEADER])


def test_keeps_a_valid_incoming_id() -> None:
    incoming = str(uuid.uuid4())
    response = _client().get("/ping", headers={CORRELATION_HEADER: incoming})
    assert response.headers[CORRELATION_HEADER] == incoming


def test_replaces_an_incoming_value_that_is_not_a_uuid() -> None:
    response = _client().get("/ping", headers={CORRELATION_HEADER: "x\n injected=1"})
    assert uuid.UUID(response.headers[CORRELATION_HEADER])


def test_request_log_line_is_written_with_status() -> None:
    with capture_logs() as logs:
        _client().get("/ping")
    (line,) = [log for log in logs if log["event"] == "http.request"]
    assert line["path"] == "/ping"
    assert line["status"] == 200
