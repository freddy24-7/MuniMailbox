# Human review and the review UI

**Status:** Stage 2 notes added, questions open · **Stages:** 2, 5 · **Requirements:** FR-18–FR-23 · **Code:** `app/web/`, `app/api/`

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
### Stage 2 — 2026-10-10

**What was built**
- [app/review/service.py](../app/review/service.py): `approve_draft()`, the only code
  path into the outbox.
- [app/web/routes.py](../app/web/routes.py) and [app/web/templates/](../app/web/templates/):
  demo login, queue, email detail, approve button, audit page.
- `OutboxMessage` table: the "sent" reply with personal data restored, stored encrypted.

**The approve rule is enforced in four places**
1. The page only shows the button to a role that may approve. (Convenience, not security.)
2. The endpoint requires the `draft:approve` permission → 403 for anyone else.
3. `approve_draft()` checks the permission again, so no other caller can skip it.
4. `outbox_messages.review_id` is required and unique: the database cannot hold an outbox
   row without a review.
Plus a test that reads the code and fails if `OutboxMessage(...)` appears anywhere else.

**Decisions and why**
- Approval is one transaction: `Review` row, outbox message, status → approved, two audit events.
- The email row is locked while approving, and the status must be "draft ready". A second
  click gets 409 Conflict and no second outbox row.
- The detail page shows three versions on purpose: the original, what the model saw, and
  the draft both restored and as stored.
- HTMX is used for one thing so far: the queue's table body re-fetches itself every 3 seconds
  (`hx-get="/queue/rows" hx-trigger="every 3s"`). The server returns HTML rows, not JSON.
- Jinja2 escapes everything by default. An email containing `<script>` is displayed as
  text; there is a test for it.

**Seen in practice**
- Team lead: can read the email, sees "Only a caseworker can approve a draft".
- Anonymous: every page redirects to the login page.

**Try it**
- Log in as each of the three roles and note what each can open.
- Approve a draft, then press the browser's back button and approve again.

**Not met yet (Stage 5)**
- Edit, reject with reason, reassign; sorting and filters; the team-lead dashboard.
- Measuring how much a caseworker changed a draft.
