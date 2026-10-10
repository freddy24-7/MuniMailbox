import logging

import structlog
from structlog.typing import Processor


def configure_logging(level: str, log_format: str) -> None:
    """Structured logs on stdout, one event per line (NFR-2).

    `merge_contextvars` adds whatever is bound for the current task, which is how every
    line gets its `correlation_id` without each call site passing it.
    """
    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if log_format == "json"
        else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelNamesMapping()[level]),
        logger_factory=structlog.PrintLoggerFactory(),
    )
