# Async Python and FastAPI

**Status:** Overview · **Stages:** 1, 2 · **Code:** `app/main.py`, `app/api/`

## In one sentence
One Python process handles many slow operations at once (database, model calls, Redis) by
pausing a task whenever it waits for I/O and running another in the meantime.

## Why it matters in this app
Nearly everything the pipeline does is waiting: for a model to answer, for ChromaDB, for
PostgreSQL. Async lets the policy and case agents run in parallel (FR-9) and lets five
emails be processed concurrently (NFR throughput) without threads.

## Core ideas
- **Coroutine and `await`** — an `async def` function can pause at each `await` and hand
  control back to the event loop.
- **Event loop** — the single scheduler that runs whichever coroutine is ready.
- **Blocking call** — any call that waits without `await` (a sync HTTP client,
  `time.sleep`, a CPU-heavy loop). It freezes *every* request, not just its own.
- **`asyncio.gather` / `TaskGroup`** — run several coroutines concurrently and wait for all.
- **FastAPI dependencies** — `Depends(...)` injects the DB session, the current user and
  the required role into each endpoint.
- **Pydantic v2 models** — validate and document request and response shapes.
- **App factory** — `create_app()` builds the app, so tests can create one with different settings.

## In this project
- All I/O is `async def`; no blocking calls in handlers (CLAUDE.md).
- Presidio and embedding models are CPU-bound and synchronous — they will need to be
  pushed off the event loop (`asyncio.to_thread`). A good first trap to meet.

## Questions to be able to answer
- What happens to other requests if one handler calls a blocking function?
- What is the difference between concurrency and parallelism, and which one does asyncio give?
- Why does an async SQLAlchemy session have to be created per request, not shared?
- How does `Depends` make role checks hard to forget?

## Related cards
[agent-orchestration](agent-orchestration.md) · [data-model-and-migrations](data-model-and-migrations.md) · [rbac](rbac.md)

## Notes from the build
_Empty — filled in during Stage 1._
