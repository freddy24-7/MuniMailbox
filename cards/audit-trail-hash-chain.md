# Tamper-evident audit trail

**Status:** Overview · **Stages:** 2, 3 · **Requirements:** GR-7, GR-8, FR-23 · **Code:** `app/audit/`, `scripts/verify_audit.py`

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
_Empty — filled in during Stage 2._
