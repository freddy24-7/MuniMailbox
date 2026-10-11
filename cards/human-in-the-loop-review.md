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

### Stage 5 — 2026-10-11 (edit, reject, reassign)

**What was built**
- [app/review/service.py](../app/review/service.py): `edit_draft`, `reject_draft` and
  `reassign_email`, next to `approve_draft`. Each checks the permission, then the scope,
  then the email's status, and writes an audit event.
- The detail page: an edit box with the restored text, a reject form with a required
  reason, and a reassign form (to a team or to one caseworker).

**Decisions and why**
- **The model's draft is never overwritten.** An edit is a `Review` row with the new
  text. "The reply as it stands" is the last edit, or else the model's text
  (`current_text`). This keeps what the model wrote next to what was sent, which the
  team-lead dashboard needs ("approved unchanged", FR-22).
- **What a caseworker types is redacted again before it is stored.** They work in the
  restored text, so it contains the citizen's details and maybe new ones. The email's
  own mapping is continued: a known value gets its old placeholder back, a new one gets
  the next number. The same goes for the reason of a rejection.
- **Approve sends the edited text**, restored at that moment.
- **Rejected is final.** No approving or editing afterwards; nothing reaches the outbox.
- **Reassigning changes who can see the email.** The person who reassigns it to another
  team gets a 404 on it a moment later. That is the scope rule working, not a bug.
- **Team leads can reassign but not edit, reject or approve.** They route work; they do
  not write replies.

**Seen in practice**
- A test caught a real ordering bug: the database stamped reviews in whole seconds, so
  "the last edit" was sometimes the first. Reviews now get their time from the application.
- Forms are parsed by hand (`parse_qs`) to avoid adding a dependency for three forms.

**Try it**
- Edit a draft and add a made-up phone number. Open "As stored (placeholders)".
- `uv run pytest tests/integration/test_review_actions.py -v`

**Still open**
- No CSRF token on the forms; the session cookie is `SameSite=Lax`.
- A reassigned email with no draft leaves only an audit event, no `Review` row.
- ~~Queue sorting and filters, the dashboard and audit search.~~ Built the same day; see below.

### Stage 5 — 2026-10-11 (queue, dashboard, audit search)

**What was built**
- **Queue (FR-18):** most urgent first and, within one urgency, the email that has
  waited longest. Filters for topic and status in the address (`/queue?topic=permits`),
  so a filtered view can be bookmarked and the 3-second refresh keeps the filter.
- **Team dashboard (FR-22):** [app/review/stats.py](../app/review/stats.py). Volume per
  topic, average handling time, share of drafts approved without changes. Team leads only.
- **Audit search (FR-23):** per email, per person and per kind of action.
- Links in the header, limited to the pages the role can open.

**Decisions and why**
- **The dashboard reuses the scope rule** (`visible_emails`), so a team lead gets their
  own team's numbers without a second rule to keep in step.
- **Counts and durations only.** The statistics never read email text, so there is
  nothing to redact and nothing to restore.
- **"Approved unchanged" falls out of an earlier decision:** edits are stored next to the
  model's draft instead of over it, so "was there an edit?" is a simple lookup.
- **A filtered audit view still verifies the whole chain.** Filtering what is shown must
  not hide a broken link.
- **Search text is matched literally.** Typing `%` finds nothing; it is not a wildcard.
- An unknown filter value is an error (422), not an empty list that looks like "no emails".

**Still open**
- Handling time is measured from arrival in the system to the decision, not from the
  moment a caseworker opened the email.
- An email with no urgency yet is sorted as "normal".
- The lists are not paged; the audit log shows at most 200 events.
