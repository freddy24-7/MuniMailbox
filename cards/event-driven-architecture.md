# Events and the event bus

**Status:** Stage 5 notes added, questions open · **Stages:** 2, 5 · **Requirements:** FR-1, FR-2 · **Code:** `app/events/`

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

### Stage 5 — 2026-10-11 (Redis Streams)

**What was built**
- [app/events/redis_bus.py](../app/events/redis_bus.py): `RedisStreamsEventBus`, behind
  the same interface as the in-memory bus. `EVENT_BUS=redis` switches to it; `memory`
  still works with nothing running.
- A Redis container in `deploy/docker-compose.yml` (about 17 MB of memory in use).
- The orchestrator became safe against receiving an event twice.

**How it works**
- **Stream** — an append-only log in Redis. `publish` is one `XADD`.
- **Consumer group** — hands each entry to one worker, and keeps a list of entries that
  were handed out and not yet acknowledged (the "pending" list).
- **Acknowledge after the work** — a worker sends `XACK` only when the handler has
  returned. If the process dies first, the entry stays pending.
- **Claim what was abandoned** — before reading new entries, a worker asks for pending
  entries that have been idle too long (`XAUTOCLAIM`) and handles those.
- **Give up eventually** — an entry delivered more than three times is dropped and
  logged, so one event that crashes its worker cannot block the stream for ever.

**Decisions and why**
- **At least once, not exactly once.** Redis can promise an event is not lost; it cannot
  promise it is delivered only once. So the receiving side has to cope: before running
  an agent, the orchestrator looks for that agent's `*.completed` audit event. If it is
  there, the work is skipped and the workflow continues from that point.
- **That same check closes the crash window.** A step is saved, the process dies before
  the next event is published. On restart the unacknowledged event comes again, the
  step is recognised as done, and the next event is published this time.
- **Events still carry identifiers only.** Nothing personal is stored in Redis.
- **The takeover time must be longer than the slowest step** (`REDIS_CLAIM_IDLE_SECONDS`,
  10 minutes). A test with a takeover time of zero showed why: a second worker took the
  event from the first while it was still busy, and it was handled twice.

**Seen in practice**
- The "Try it" from Stage 2 now ends differently: an event that was being handled when
  the bus stopped is handled by the next one that starts
  (`test_an_event_that_was_being_handled_when_the_app_died_is_handled_after_restart`).
- The Redis tests need a real Redis and are skipped without `TEST_REDIS_URL`. CI has one;
  locally they were run against the container.

**Still open**
- Recovery waits for the takeover time: after a crash, an email continues up to 10
  minutes later, not at once.
- The check "already completed?" and the agent's work are not one atomic step. Two
  workers handling the same event at the same moment could both run it; the takeover
  time is what makes that unlikely.
- A dropped event is only a log line. Nothing marks the email "needs attention" yet.
