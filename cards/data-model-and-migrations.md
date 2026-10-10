# Data model, SQLAlchemy and Alembic

**Status:** Overview · **Stages:** 1, 3 · **Code:** `app/db/`, Alembic migrations

## In one sentence
The relational tables are described once as Python classes, accessed through repositories,
and changed over time through versioned migration scripts.

## Why it matters in this app
The data model *is* the privacy design: which table holds clear text, which holds
placeholders and which holds encrypted values decides what can leak. It also carries the
workflow (email status), the human decision (`Review`) and the evidence (`AuditEvent`).

## Core ideas
- **ORM model** — a Python class mapped to a table (SQLAlchemy 2.x, typed `Mapped[...]` columns).
- **Async session** — a unit of work; changes are committed together or not at all.
- **Repository** — a small class that hides queries, so agents and endpoints never write SQL.
- **Migration** — a script that moves the schema from one version to the next (Alembic);
  applied migrations are never edited, a new one is added.
- **Schema vs model** — Pydantic schemas are what crosses the API; ORM models are what is stored.
- **State machine** — the email status has a fixed set of values and allowed transitions (FR-4).

## In this project
Entities from the PRD: `Citizen`, `Case`, `Email`, `PiiMapping`, `Draft`, `Review`, `User`,
`AuditEvent` (plus `DocumentChunk` in ChromaDB).
- `Email.body_redacted` holds placeholders; the original body is stored encrypted.
- `Draft.text_redacted` holds placeholders only (acceptance criterion).
- `AuditEvent` is append-only — no update or delete path exists in its repository.
- One email can have several drafts; one draft can have several reviews.

## Questions to be able to answer
- Which columns in which tables contain personal data, and in what form?
- Why is a schema change a migration instead of an edit to the model alone?
- What differs between SQLite and PostgreSQL that could bite (concurrency, types, JSON)?
- Which status transitions are allowed, and where is that enforced?

## Related cards
[encryption-and-retention](encryption-and-retention.md) · [audit-trail-hash-chain](audit-trail-hash-chain.md) · [synthetic-data](synthetic-data.md)

## Notes from the build
_Empty — filled in during Stage 1._
