# Role-based access control (RBAC)

**Status:** Stage 3 notes added, questions open · **Stages:** 3, 5 · **Requirements:** GR-5, GR-6 · **Code:** `app/auth/`, `app/api/`

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
### Stage 2 — 2026-10-10 (roles and permissions arrived early; team scoping has not)

**What was built**
- [app/auth/permissions.py](../app/auth/permissions.py): five permissions and the one
  table that maps the three roles to them.
- [app/auth/dependencies.py](../app/auth/dependencies.py): `current_user` reads the login
  cookie; `require(Permission.X)` is what an endpoint declares.
- [app/auth/session.py](../app/auth/session.py): the login cookie is the user's ID,
  encrypted and signed with `SESSION_SECRET`, valid for eight hours.

**Decisions and why**
- Code asks for a permission (`draft:approve`), never for a role name. Changing who may do
  what is an edit to one table.
- The same permission is checked at the endpoint and again in the service behind it
  (`approve_draft`, `restore`).
- A cookie that was altered, forged or has expired decodes to "nobody".
- [tests/unit/test_architecture.py](../tests/unit/test_architecture.py) lists the public
  paths (health and login). Any other endpoint without a logged-in user fails the test,
  so a new endpoint cannot forget its check unnoticed.

**Seen in practice**
- Auditor: 403 on the queue and on an email, 200 on the audit page.
- Team lead: can read, gets 403 on approve.

**Not met yet (Stage 3)**
- Object-level checks: today every caseworker and team lead sees **every** email.
  "Is this email assigned to me or my team?" is not asked anywhere yet (GR-6).

### Stage 3 — 2026-10-10 (object-level access)

**What was built**
- [app/auth/scope.py](../app/auth/scope.py): the one place that answers "may this user
  touch *this* email?". The same rule exists twice in that file: `can_access_email()`
  for one email, `visible_emails()` as a query filter for lists.
- `Email.team`, set when the email is ingested.

**The rule**
| Role | Sees an email when |
| --- | --- |
| Caseworker | it is in their team, or assigned to them personally |
| Team lead | it is in their team, or has no team yet (theirs to route) |
| Auditor | never (they see the audit log instead) |

**Decisions and why**
- Two questions, two modules. `permissions.py`: may this role approve drafts *at all*?
  `scope.py`: may this user approve *this* draft? Checking only the first is the classic
  IDOR bug — change the ID in the URL and read someone else's data.
- Checked in four places: the queue query, the detail page, `approve_draft()` and
  `restore()`. The last two are services, so a future endpoint cannot forget it.
- **404, not 403**, for an email outside your team. A 403 would confirm that an email
  with that ID exists.
- "No team" never matches "no team": an auditor (team = none) does not get access to an
  unrouted email (team = none). There is a test for exactly that.

**Seen in practice**
- The test user for this is a caseworker with every permission, in another team. Only
  the scope differs, so only the scope can be what stops them.
- The two emails ingested before this stage have no team and vanished from caseworker
  queues. Team leads still see them.

**Try it**
- Log in as a caseworker of one team, open an email, copy the URL. Log in as a
  caseworker of the other team and paste it.

**Still open**
- Until the intake agent routes by topic (Stage 4), an email belongs to the team of
  whoever fetched it. Reassigning is Stage 5.
