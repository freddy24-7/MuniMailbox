# Async Python and FastAPI

**Status:** Stage 1 notes added, questions open · **Stages:** 1, 2 · **Code:** `app/main.py`, `app/api/`

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
### Stage 1 — 2026-10-10

**What was built**
- [app/main.py](../app/main.py): `create_app(settings)` builds the app. Tests call it with
  their own settings; `uvicorn app.main:app` uses the module-level `app = create_app()`.
- `lifespan` in the same file creates the database engine **once** at startup and
  disposes it at shutdown. The engine owns the connection pool.
- [app/db/session.py](../app/db/session.py): `get_session` is a dependency that opens
  **one session per request** from that shared engine and closes it afterwards.
- [app/api/health.py](../app/api/health.py): `readyz` receives its session through
  `Annotated[AsyncSession, Depends(get_session)]` — the endpoint never builds one itself.

**Decisions and why**
- Engine per app, session per request. An engine is safe to share; a session holds one
  transaction's state and is not.
- The correlation middleware is written as plain ASGI (`__call__(scope, receive, send)`)
  instead of Starlette's `BaseHTTPMiddleware`, which runs the endpoint in a separate task
  and makes context variables unreliable — and the correlation ID lives in one.
- Because `app` is built at import time, anything importing `app.main` needs a valid
  `DATABASE_URL`. That is why [tests/conftest.py](../tests/conftest.py) sets it before its imports.

**Seen in practice**
- With PostgreSQL stopped, `/healthz` still returns 200 and `/readyz` returns 500. That is
  the difference between liveness ("process is up") and readiness ("can do work").
  A tidy 503 instead of an unhandled 500 is a possible later improvement.

**Try it**
- `uv run uvicorn app.main:app --reload`, then open `http://localhost:8000/docs`.
- `docker compose stop postgres`, call both health endpoints, `docker compose start postgres`.
- Add `time.sleep(5)` to `healthz`, call `/healthz` and `/readyz` at the same time from two
  terminals and watch the second wait. Change it to `await asyncio.sleep(5)` and repeat.

**Not met yet**
- No blocking library so far. Presidio (Stage 2) is the first one that must be moved off the event loop.
- No `asyncio.gather` yet; the first real use is policy ‖ case in Stage 4.
