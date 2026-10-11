import json
import logging
from pathlib import Path
from typing import IO

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger


class _JsonLinesFile:
    """Processor that also writes every event to a file, one JSON object per line.

    This is the copy a log shipper reads and sends to Loki (NFR-2). It is always JSON,
    whatever the console shows, and it holds exactly the fields of the event: the same
    rule applies as everywhere else, no email text and no personal data in a log line.
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file: IO[str] = path.open("a", encoding="utf-8", buffering=1)

    def __call__(self, _: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
        self._file.write(json.dumps(event_dict, default=str, ensure_ascii=False) + "\n")
        return event_dict


def configure_logging(level: str, log_format: str, log_file: Path | None = None) -> None:
    """Structured logs on stdout, one event per line (NFR-2).

    `merge_contextvars` adds whatever is bound for the current task, which is how every
    line gets its `correlation_id` without each call site passing it.
    """
    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if log_format == "json"
        else structlog.dev.ConsoleRenderer()
    )
    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.format_exc_info,
    ]
    if log_file is not None:
        processors.append(_JsonLinesFile(log_file))
    structlog.configure(
        processors=[*processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelNamesMapping()[level]),
        logger_factory=structlog.PrintLoggerFactory(),
    )
