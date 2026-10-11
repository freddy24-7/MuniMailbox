"""The Redis Streams event bus, against a real Redis. Skipped without TEST_REDIS_URL
(CI sets it; locally: `docker compose up -d redis` and
`TEST_REDIS_URL=redis://localhost:6379/15 uv run pytest tests/integration/test_redis_bus.py`)."""

import asyncio
import os
import uuid
from collections.abc import AsyncIterator

import pytest
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import AuditEvent, Draft, Email, EmailStatus
from app.events.bus import Event
from app.events.redis_bus import RedisStreamsEventBus
from app.main import create_app
from app.orchestrator.ingestion import ingest_email
from app.privacy.redaction import Redactor
from app.rag.store import InMemoryVectorStore
from app.services import Services
from tests.helpers import FakeEmbedder, FakeLLM, incoming_email

REDIS_URL = os.environ.get("TEST_REDIS_URL")
TAKEOVER_MS = 100
pytestmark = pytest.mark.skipif(REDIS_URL is None, reason="needs Redis: set TEST_REDIS_URL")


@pytest.fixture
async def stream() -> AsyncIterator[str]:
    """A stream of its own per test, removed afterwards."""
    name = f"test:{uuid.uuid4().hex}"
    yield name
    redis = Redis.from_url(REDIS_URL or "")
    await redis.delete(name)
    await redis.aclose()


def _bus(stream: str, **options: int) -> RedisStreamsEventBus:
    return RedisStreamsEventBus(REDIS_URL or "", stream=stream, workers=2, block_ms=50, **options)


def _event(name: str = "x.happened") -> Event:
    return Event(name, uuid.uuid4(), uuid.uuid4())


async def test_events_reach_their_subscribers_and_events_they_cause_are_waited_for(
    stream: str,
) -> None:
    bus = _bus(stream)
    seen: list[Event] = []

    async def first(event: Event) -> None:
        await bus.publish(Event("second", event.email_id, event.correlation_id))

    async def second(event: Event) -> None:
        seen.append(event)

    bus.subscribe("first", first)
    bus.subscribe("second", second)
    sent = _event("first")
    await bus.publish(sent)  # before start: must not be lost
    await bus.publish(_event("nobody.listens"))
    await bus.start()
    await bus.join()
    await bus.stop()

    assert [(event.name, event.email_id) for event in seen] == [("second", sent.email_id)]


async def test_a_failing_handler_does_not_stop_the_bus(stream: str) -> None:
    bus = _bus(stream)
    seen: list[str] = []

    async def handler(event: Event) -> None:
        seen.append("called")
        if len(seen) == 1:
            raise RuntimeError("boom")

    bus.subscribe("x.happened", handler)
    await bus.start()
    await bus.publish(_event())
    await bus.publish(_event())
    await bus.join()
    await bus.stop()

    assert seen == ["called", "called"]


async def _crash_while_handling(stream: str, event: Event) -> None:
    """A process that takes the event and dies before finishing it."""
    started = asyncio.Event()

    async def never_finishes(_: Event) -> None:
        started.set()
        await asyncio.Event().wait()

    doomed = _bus(stream)
    doomed.subscribe(event.name, never_finishes)
    await doomed.start()
    await doomed.publish(event)
    await asyncio.wait_for(started.wait(), timeout=5)
    await doomed.stop()  # cancelled mid-handler: the event was never acknowledged
    # Long enough to count as abandoned. (At 0 a second worker would take the event over
    # from the first while it is still busy with it, and it would be handled twice.)
    await asyncio.sleep(TAKEOVER_MS * 2 / 1000)


async def test_an_event_that_was_being_handled_when_the_app_died_is_handled_after_restart(
    stream: str,
) -> None:
    """What the in-memory bus could not do: this is the email stuck in "processing"."""
    event = _event()
    await _crash_while_handling(stream, event)

    restarted = _bus(stream, claim_idle_ms=TAKEOVER_MS)
    seen: list[uuid.UUID] = []

    async def handler(received: Event) -> None:
        seen.append(received.email_id)

    restarted.subscribe(event.name, handler)
    await restarted.start()
    await restarted.join()
    await restarted.stop()

    assert seen == [event.email_id]


async def test_an_event_that_keeps_killing_its_worker_is_given_up_on(stream: str) -> None:
    event = _event()
    await _crash_while_handling(stream, event)

    restarted = _bus(stream, claim_idle_ms=TAKEOVER_MS, max_deliveries=1)
    seen: list[uuid.UUID] = []

    async def handler(received: Event) -> None:
        seen.append(received.email_id)

    restarted.subscribe(event.name, handler)
    await restarted.start()
    await restarted.join()  # returns: the event was dropped, not left pending for ever
    await restarted.stop()

    assert seen == []


async def test_the_whole_pipeline_runs_over_redis(
    settings: Settings,
    engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    redactor: Redactor,
    stream: str,
) -> None:
    over_redis = settings.model_copy(
        update={
            "event_bus": "redis",
            "redis_url": SecretStr(REDIS_URL or ""),
            "redis_stream": stream,
        }
    )
    app = create_app(
        over_redis,
        local_llm=FakeLLM(),
        embedder=FakeEmbedder(),
        vector_store=InMemoryVectorStore(),
        redactor=redactor,
    )
    async with app.router.lifespan_context(app):
        services: Services = app.state.services
        assert isinstance(services.bus, RedisStreamsEventBus)
        async with services.session_factory() as session:
            email = await ingest_email(session, services.bus, services.cipher, incoming_email())
        await services.bus.join()

    async with session_factory() as session:
        stored = await session.get_one(Email, email.id)
        actions = list(
            await session.scalars(
                select(AuditEvent.action)
                .where(AuditEvent.email_id == email.id)
                .order_by(AuditEvent.id)
            )
        )
        drafts = (await session.scalars(select(Draft))).all()
    assert stored.status is EmailStatus.DRAFT_READY
    assert len(drafts) == 1
    assert actions[-1] == "quality.completed"
    assert len(actions) == 10
