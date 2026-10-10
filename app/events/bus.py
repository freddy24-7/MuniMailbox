import asyncio
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class Event:
    """A fact that happened. Carries identifiers only, never email content: events are
    logged, queued and (later) stored in Redis, none of which may hold personal data."""

    name: str
    email_id: uuid.UUID
    correlation_id: uuid.UUID


Handler = Callable[[Event], Awaitable[None]]


class EventBus(Protocol):
    async def publish(self, event: Event) -> None: ...

    def subscribe(self, name: str, handler: Handler) -> None: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


class InMemoryEventBus:
    """In-process queue for tests and the first slice. Events are lost if the process
    stops; Redis Streams replaces this behind the same interface."""

    def __init__(self, workers: int = 5) -> None:
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._workers = workers
        self._tasks: list[asyncio.Task[None]] = []

    def subscribe(self, name: str, handler: Handler) -> None:
        self._handlers[name].append(handler)

    async def publish(self, event: Event) -> None:
        await self._queue.put(event)

    async def start(self) -> None:
        self._tasks = [asyncio.create_task(self._work()) for _ in range(self._workers)]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    async def join(self) -> None:
        """Wait until every published event, and every event those caused, is handled."""
        await self._queue.join()

    async def _work(self) -> None:
        while True:
            event = await self._queue.get()
            try:
                with structlog.contextvars.bound_contextvars(
                    correlation_id=str(event.correlation_id), email_id=str(event.email_id)
                ):
                    for handler in self._handlers[event.name]:
                        try:
                            await handler(event)
                        except Exception:
                            # One failing handler must not stop the worker.
                            logger.exception("event.handler_failed", event_name=event.name)
            finally:
                self._queue.task_done()
