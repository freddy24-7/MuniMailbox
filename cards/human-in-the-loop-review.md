# Human review and the review UI

**Status:** Overview · **Stages:** 2, 5 · **Requirements:** FR-18–FR-23 · **Code:** `app/web/`, `app/api/`

## In one sentence
The system only proposes; a caseworker reads the draft next to its evidence, edits it,
and takes the decision — and nothing reaches the outbox without that decision.

## Why it matters in this app
Human approval is the last defence against a hallucinated rule or a wrong tone, and it
keeps responsibility with the municipality. "Sending replies automatically" is an
explicit non-goal.

## Core ideas
- **Human-in-the-loop** — a person must act before the outcome takes effect (as opposed
  to merely being able to intervene).
- **Decision as data** — approve, edit, reject (with reason) and reassign are stored as
  `Review` rows, not just button clicks.
- **Hard invariant** — no code path moves a draft to the outbox without a `Review` with
  action `approve` by a user with the caseworker role.
- **Evidence beside the draft** — original email, classification, case history, sources
  and quality warnings side by side (FR-19), so checking is fast.
- **Automation bias** — people tend to accept a plausible machine proposal. The UI should
  make verifying easier than rubber-stamping.
- **Server-rendered UI with HTMX** — the server returns HTML fragments; HTMX swaps them
  into the page. No separate frontend build, no client-side state.
- **Simulated outbox** — "sent" means a row in a table (FR-21).

## In this project
- Queue sorted by urgency and age, filterable by topic and status (FR-18).
- Caseworkers see restored personal data; the stored draft keeps placeholders.
- Team-lead dashboard: volume per topic, handling time, share approved unchanged (FR-22).
- Auditor view: searchable audit log, no clear-text content (FR-23).
- Success metrics measured here: ≥ 40% approved unchanged, ≥ 80% with minor edits.

## Questions to be able to answer
- Where exactly is the "approve before outbox" rule enforced — UI, API or database?
- How do you measure "minor edit" versus "rewrote it"?
- What in the detail view helps a caseworker catch an unsupported claim?
- When an edited draft is saved, in what form is the edited text stored?

## Related cards
[rbac](rbac.md) · [grounded-drafting-and-quality](grounded-drafting-and-quality.md) · [encryption-and-retention](encryption-and-retention.md) · [audit-trail-hash-chain](audit-trail-hash-chain.md)

## Notes from the build
_Empty — filled in during Stage 2._
