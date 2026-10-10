# Tamper-evident audit trail

**Status:** Stage 2 notes added, questions open · **Stages:** 2, 3 · **Requirements:** GR-7, GR-8, FR-23 · **Code:** `app/audit/`, `scripts/verify_audit.py`

## In one sentence
Every pipeline step and human action is written to a log that can only grow, and each
entry is cryptographically tied to the one before it, so any later change is detectable.

## Why it matters in this app
The auditor's question is "can you show me what happened to this email, and can I trust
the answer?" Logging answers the first half; the hash chain answers the second.

## Core ideas
- **Audit log vs application log** — an application log helps developers debug; an audit
  log is evidence for accountability and must be complete and trustworthy.
- **Append-only** — rows are inserted, never updated or deleted.
- **Cryptographic hash** — a fixed-size fingerprint (e.g. SHA-256); change one character
  of the input and the fingerprint changes completely.
- **Hash chain** — each record stores the hash of the previous record. Editing record 10
  breaks the link from record 11 onwards.
- **Canonical serialization** — the record must be turned into bytes the same way every
  time, or verification fails for the wrong reasons.
- **Tamper-evident, not tamper-proof** — the chain reveals changes; it does not prevent
  them. Someone who can rewrite the *whole* chain can still cheat unless the latest hash
  is anchored somewhere else.
- **Actor** — an agent or a user; every event has one.

## In this project
- Fields: timestamp, actor, email ID, action, model used, `left_local`, `prev_hash` (GR-7).
- `scripts/verify_audit.py` walks the chain and reports the first broken link.
- Acceptance criterion: tampering with an audit record is detected.
- Audit rows hold no clear-text email content — auditors read them.

## Questions to be able to answer
- Five emails are processed concurrently: how is the order of the chain decided?
- What is deliberately *not* stored in an audit event, and why?
- Where does the chain start (the first record's `prev_hash`)?
- How does the retention delete of a PII mapping coexist with a log that never deletes?
- How could the latest hash be anchored outside the database?

## Related cards
[rbac](rbac.md) · [inference-routing](inference-routing.md) · [observability](observability.md) · [data-model-and-migrations](data-model-and-migrations.md)

## Notes from the build
### Stage 2 — 2026-10-10

**What was built**
- [app/audit/writer.py](../app/audit/writer.py): `record()` appends an event,
  `verify_chain()` walks the chain, `compute_hash()` is shared by both.
- [scripts/verify_audit.py](../scripts/verify_audit.py): the same check from the command
  line; exit code 1 if the chain is broken.
- An `/audit` page for the auditor role, with the chain status at the top.

**How one record is made**
1. Read the hash of the last record (or 64 zeros for the very first, the "genesis" value).
2. Build a canonical JSON of this record's fields plus that previous hash: sorted keys,
   fixed separators, timestamp in UTC with microseconds.
3. SHA-256 of those bytes is this record's `hash`.

**Decisions and why**
- `record()` does not commit; the caller does. The audit event and the change it
  describes are saved together or not at all.
- Concurrency: two pipelines appending at once would both read the same "last" record.
  On PostgreSQL an advisory lock makes appends take turns. As a second line, `prev_hash`
  has a unique constraint, so the database itself rejects a fork.
- Append-only at application level: the ORM raises `AuditImmutableError` on any update or
  delete of an audit row.
- Actors are named `agent:privacy`, `user:<id>`, `system:intake`. Viewing an email with
  restored data is itself an event (`email.viewed_restored`).
- No free-text field. An audit row cannot contain email content because there is nowhere to put it.

**Seen in practice**
- Changing one row with raw SQL: `verify_chain` reports that row. Deleting a row: it
  reports the *next* row, whose `prev_hash` now points at nothing.
- The unique constraint caught a real race while testing on SQLite (which has no advisory
  lock): one of two simultaneous appends was rejected instead of silently forking the chain.

**Try it**
- `uv run python scripts/verify_audit.py`
- Tamper on purpose, then verify again:
  `docker compose exec postgres psql -U munimailbox -c "UPDATE audit_events SET action='x' WHERE id=3"`
  (Your chain stays broken afterwards. That is the point; to start clean, recreate the database.)

**Not met yet (Stage 3)**
- A database trigger that blocks updates and deletes for everyone but a superuser.
- Deleting the *last* row is not detectable from the chain alone; that needs the latest
  hash anchored somewhere outside the database.
