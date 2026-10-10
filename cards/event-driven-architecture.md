# Events and the event bus

**Status:** Overview · **Stages:** 2, 5 · **Requirements:** FR-1, FR-2 · **Code:** `app/events/`

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
_Empty — filled in during Stage 2._
