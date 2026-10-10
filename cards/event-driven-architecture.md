# Events and the event bus

**Status:** Stage 2 notes added, questions open · **Stages:** 2, 5 · **Requirements:** FR-1, FR-2 · **Code:** `app/events/`

## In one sentence
Components announce what happened ("email received", "redaction completed") instead of
calling each other directly, and whoever cares subscribes.

## Why it matters in this app
An email takes up to a minute to process; the request that ingests it cannot wait. Events
decouple intake from processing, make every step visible, and let a step be retried
without redoing the ones before it.

## Core ideas
- **Event** — an immutable fact in the past tense, with a name and a payload (`email.received`).
- **Publisher / subscriber** — the sender does not know who listens.
- **Event bus interface** — `publish` and `subscribe`; the code depends on the interface,
  not on Redis.
- **Redis Streams** — an append-only log in Redis. `XADD` appends; a **consumer group**
  shares the work between workers; `XACK` confirms an entry was handled; unacknowledged
  entries stay in a pending list and can be claimed again.
- **At-least-once delivery** — a message may arrive twice after a crash, so handlers must
  be **idempotent** (doing it twice has the same effect as once).
- **Correlation ID** — one ID carried by every event of the same email.
- **Queue vs stream** — a queue forgets a message once consumed; a stream keeps history.

## In this project
- `email.received` starts the workflow; each step emits its own completion event (FR-2).
- Two implementations behind one interface: an in-process async queue (tests, thin slice)
  and Redis Streams (Stage 5).
- "No email lost" (NFR reliability) depends on acknowledging only after the work is saved.

## Questions to be able to answer
- What is lost when the app crashes with an in-process queue? With Redis Streams?
- What makes a handler idempotent, and which of our steps are not naturally so?
- What belongs in an event payload — the email text, or just its ID? Why does that matter for privacy?
- When is a direct function call the better choice than an event?

## Related cards
[agent-orchestration](agent-orchestration.md) · [observability](observability.md) · [async-python-fastapi](async-python-fastapi.md)

## Notes from the build
### Stage 2 — 2026-10-10

**What was built**
- [app/events/bus.py](../app/events/bus.py): the `EventBus` interface (`publish`,
  `subscribe`, `start`, `stop`) and `InMemoryEventBus`, an `asyncio.Queue` with five
  worker tasks that take events off the queue and call the subscribed handlers.
- Events so far: `email.received`, `privacy.completed`, `drafting.completed`.

**Decisions and why**
- An `Event` has three fields: name, email ID, correlation ID. **No content.** Events get
  logged and queued, and later stored in Redis; none of those may hold personal data.
  A handler that needs the email reads it from the database.
- Publish **after** commit ([app/orchestrator/ingestion.py](../app/orchestrator/ingestion.py)).
  Published before, a fast subscriber could look for an email that is not saved yet.
- The worker binds the event's correlation ID to the logging context, the same way the
  HTTP middleware does for requests. Every log line of the pipeline carries the email's ID.
- A handler that raises is logged and the worker carries on; one bad email must not stop the queue.

**Seen in practice**
- Clicking "Fetch next email" returns at once; the draft appears about 30 seconds later.
  The page polls every 3 seconds for the new status. That gap *is* the decoupling.
- `bus.join()` waits until the queue is empty, including events published by handlers.
  The tests use it instead of sleeping.

**Try it**
- Fetch an email and stop the app (`Ctrl+C`) while it says "processing". Start it again:
  the email stays in "processing" for ever. The event was only in memory.
- `uv run pytest tests/unit/test_bus.py -v`

**Not met yet (Stage 5)**
- Redis Streams, consumer groups, acknowledgements, redelivery after a crash.
- Idempotency is only partly there: the privacy agent skips an email that already has a
  redaction record, the drafting agent would write a second draft.
