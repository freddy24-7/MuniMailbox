import json
import uuid

import pytest
import structlog

from app.observability.logging import configure_logging


def test_json_log_line_carries_the_bound_correlation_id(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO", "json")
    correlation_id = str(uuid.uuid4())

    with structlog.contextvars.bound_contextvars(correlation_id=correlation_id):
        structlog.get_logger().info("agent.completed", agent="privacy")
    structlog.get_logger().debug("below the configured level")

    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["event"] == "agent.completed"
    assert event["correlation_id"] == correlation_id
    assert event["level"] == "info"
    assert "timestamp" in event
