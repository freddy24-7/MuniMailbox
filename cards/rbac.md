# Role-based access control (RBAC)

**Status:** Overview · **Stages:** 3, 5 · **Requirements:** GR-5, GR-6 · **Code:** `app/auth/`, `app/api/`

## In one sentence
What a user may do and see is decided by their role, the mapping from roles to
permissions is written in exactly one place, and every endpoint checks it.

## Why it matters in this app
The three roles have deliberately different views of the same email: the caseworker sees
the person, the team lead sees the team, the auditor sees the process but not the person.

## Core ideas
- **Authentication vs authorization** — who you are versus what you may do. This project
  has a demo login (no DigiD) but real authorization.
- **Role → permission** — code checks a permission (`draft:approve`), not a role name, so
  roles can change without touching endpoints.
- **Single source of truth** — one module defines all roles and permissions (GR-5).
- **Deny by default** — an endpoint without a declared permission is an error, not public.
- **Function-level vs object-level access** — "may approve drafts" is not enough; it must
  be "may approve *this* draft". Checking only the first is the classic IDOR bug.
- **Least privilege** — each role gets only what its job needs.
- **Separation of duties** — the person who audits is not the person who approves.
- **Field-level filtering** — the same record is serialized differently per role.

## In this project
| Role | Can do | Can see |
| --- | --- | --- |
| Caseworker | Review, edit, approve, reject, reassign | Own/team emails, restored PII, sources, case history |
| Team lead | Assign, monitor, handle escalations | Whole team's queue, statistics, all drafts |
| Auditor | Inspect | Audit log and redaction records, no clear-text content |

Acceptance criterion: an auditor cannot view restored personal data; a caseworker cannot
view another team's emails.

## Questions to be able to answer
- Where is the object-level check ("is this email in my team?") — endpoint, repository or query?
- Why is hiding a button in the UI not access control?
- How do you write a test that fails when a new endpoint forgets its role?
- What should an unauthorized request get back: 403 or 404? Why might it matter?

## Related cards
[encryption-and-retention](encryption-and-retention.md) · [audit-trail-hash-chain](audit-trail-hash-chain.md) · [human-in-the-loop-review](human-in-the-loop-review.md) · [async-python-fastapi](async-python-fastapi.md)

## Notes from the build
_Empty — filled in during Stage 3._
