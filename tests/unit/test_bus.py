import uuid

from app.events.bus import Event, InMemoryEventBus


def _event(name: str) -> Event:
    return Event(name, uuid.uuid4(), uuid.uuid4())


async def test_subscribers_receive_only_their_events() -> None:
    bus = InMemoryEventBus(workers=2)
    seen: list[str] = []

    async def handler(event: Event) -> None:
        seen.append(event.name)

    bus.subscribe("a.happened", handler)
    await bus.start()
    await bus.publish(_event("a.happened"))
    await bus.publish(_event("b.happened"))
    await bus.join()
    await bus.stop()

    assert seen == ["a.happened"]


async def test_join_waits_for_events_published_by_handlers() -> None:
    bus = InMemoryEventBus()
    seen: list[str] = []

    async def first(event: Event) -> None:
        await bus.publish(Event("second", event.email_id, event.correlation_id))

    async def second(event: Event) -> None:
        seen.append(event.name)

    bus.subscribe("first", first)
    bus.subscribe("second", second)
    await bus.start()
    await bus.publish(_event("first"))
    await bus.join()
    await bus.stop()

    assert seen == ["second"]


async def test_a_failing_handler_does_not_stop_the_bus() -> None:
    bus = InMemoryEventBus(workers=1)
    seen: list[str] = []

    async def handler(event: Event) -> None:
        if not seen:
            seen.append("failed")
            raise RuntimeError("boom")
        seen.append("handled")

    bus.subscribe("x", handler)
    await bus.start()
    await bus.publish(_event("x"))
    await bus.publish(_event("x"))
    await bus.join()
    await bus.stop()

    assert seen == ["failed", "handled"]
