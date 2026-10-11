"""The event bus on Redis Streams.

A stream is an append-only log in Redis. A consumer group hands each entry to one
worker and remembers which entries were handed out but not yet acknowledged. That list
is what survives a crash: when the app starts again, entries nobody acknowledged are
claimed and handled, so an email no longer stays in "processing" for good.

Delivery is at least once, not exactly once. The orchestrator is built for that: a step
that already ran is not run again (see `Orchestrator._run_once`).
"""

import asyncio
import socket
import uuid
from collections import defaultdict
from typing import Any

import structlog
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from app.events.bus import Event, Handler

logger = structlog.get_logger(__name__)


class RedisStreamsEventBus:
    def __init__(
        self,
        url: str,
        *,
        stream: str,
        group: str = "orchestrator",
        workers: int = 5,
        claim_idle_ms: int = 60_000,
        max_deliveries: int = 3,
        block_ms: int = 1000,
    ) -> None:
        # Entries are identifiers only (see `Event`), so nothing personal is stored in Redis.
        self._redis: Redis = Redis.from_url(url, decode_responses=True)
        self._stream, self._group = stream, group
        self._workers = workers
        # How long an entry may sit unacknowledged before another worker takes it over.
        self._claim_idle_ms = claim_idle_ms
        # An entry that keeps killing its worker is dropped after this many tries.
        self._max_deliveries = max_deliveries
        self._block_ms = block_ms
        self._consumer = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._tasks: list[asyncio.Task[None]] = []
        self._in_flight = 0

    def subscribe(self, name: str, handler: Handler) -> None:
        self._handlers[name].append(handler)

    async def publish(self, event: Event) -> None:
        await self._redis.xadd(
            self._stream,
            {
                "name": event.name,
                "email_id": str(event.email_id),
                "correlation_id": str(event.correlation_id),
            },
        )

    async def start(self) -> None:
        try:
            # "0": the group starts at the beginning of the stream, so events published
            # before the first start are not skipped.
            await self._redis.xgroup_create(self._stream, self._group, id="0", mkstream=True)
        except ResponseError as error:
            if "BUSYGROUP" not in str(error):  # the group exists already: fine
                raise
        self._tasks = [asyncio.create_task(self._work()) for _ in range(self._workers)]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        await self._redis.aclose()

    async def join(self) -> None:
        """Wait until nothing is waiting, nothing is being handled and nothing is
        unacknowledged. For tests and scripts; a running app never needs it."""
        while True:
            groups = await self._redis.xinfo_groups(self._stream)
            group = next(g for g in groups if g["name"] == self._group)
            if not group["pending"] and not group.get("lag") and not self._in_flight:
                return
            await asyncio.sleep(0.02)

    async def _work(self) -> None:
        while True:
            try:
                entries = await self._abandoned() or await self._new()
                for entry_id, fields in entries:
                    await self._handle(entry_id, fields)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Redis unreachable, for instance: keep trying, do not kill the worker.
                logger.exception("event.bus_error")
                await asyncio.sleep(1)

    async def _new(self) -> list[tuple[str, dict[str, str]]]:
        # redis-py's replies are typed loosely; the shape is [(stream, [(id, fields), ...])].
        reply: Any = await self._redis.xreadgroup(
            self._group, self._consumer, {self._stream: ">"}, count=1, block=self._block_ms
        )
        return [entry for _, entries in reply or [] for entry in entries]

    async def _abandoned(self) -> list[tuple[str, dict[str, str]]]:
        """Entries a worker took and never acknowledged: it crashed, or was stopped."""
        reply: Any = await self._redis.xautoclaim(
            self._stream, self._group, self._consumer, min_idle_time=self._claim_idle_ms, count=1
        )
        claimed: list[tuple[str, dict[str, str]]] = []
        for entry_id, fields in reply[1]:
            pending: list[dict[str, Any]] = await self._redis.xpending_range(
                self._stream, self._group, min=entry_id, max=entry_id, count=1
            )
            if pending and pending[0]["times_delivered"] > self._max_deliveries:
                logger.error("event.dropped_after_retries", entry_id=entry_id, **fields)
                await self._redis.xack(self._stream, self._group, entry_id)
                continue
            claimed.append((entry_id, fields))
        return claimed

    async def _handle(self, entry_id: str, fields: dict[str, str]) -> None:
        self._in_flight += 1
        try:
            event = Event(
                fields["name"], uuid.UUID(fields["email_id"]), uuid.UUID(fields["correlation_id"])
            )
            with structlog.contextvars.bound_contextvars(
                correlation_id=str(event.correlation_id), email_id=str(event.email_id)
            ):
                for handler in self._handlers[event.name]:
                    try:
                        await handler(event)
                    except Exception:
                        # One failing handler must not stop the worker.
                        logger.exception("event.handler_failed", event_name=event.name)
            # Acknowledged only now. If the process dies before this line, the entry
            # stays pending and is claimed again after `claim_idle_ms`.
            await self._redis.xack(self._stream, self._group, entry_id)
        finally:
            self._in_flight -= 1
