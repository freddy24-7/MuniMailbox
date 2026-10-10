# Testing strategy and quality gates

**Status:** Stage 1 notes added, questions open · **Stages:** all · **Requirements:** GR-4, NFR maintainability · **Code:** `tests/unit`, `tests/integration`, `tests/e2e`, `tests/pii_testset`

## In one sentence
Different kinds of tests answer different questions, and a few of them act as gates that
stop a build when a rule of the system is broken.

## Why it matters in this app
The important promises here are negative ones: PII *cannot* reach the cloud, an auditor
*cannot* see restored data, a draft *cannot* be sent without approval. Those are only
true if a test tries to break them and fails.

## Core ideas
- **Unit test** — one function or class, no network, no database. Fast.
- **Integration test** — several real parts together (API + DB + in-memory event bus), LLM mocked.
- **End-to-end test** — the whole app driven through a browser (Playwright).
- **Mocking the LLM** — tests must be deterministic and free; a fake client returns canned text.
- **Bypass test** — a test whose job is to prove a rule cannot be circumvented.
- **Metric gate** — a test that computes a number (recall) and fails below a threshold.
- **Coverage** — how much code the tests execute; useful as a floor, misleading as a goal.
- **Test-first for misses** — when a redaction miss is found, add the case, watch it fail, then fix.

## In this project
- New agent or endpoint → unit tests plus at least one integration test.
- Privacy, RBAC or audit change → a test proving the rule cannot be bypassed.
- `uv run pytest tests/pii_testset` — recall must stay ≥ the PRD threshold (95%, to be confirmed).
- Classification accuracy target on a test set: ≥ 90%.
- Coverage target for core modules: ≥ 70%.
- The seven PRD acceptance criteria each become an automated test.

## Questions to be able to answer
- Why is recall the gated metric for redaction and not precision?
- What does a mocked LLM test prove, and what does it not prove?
- How would you test "the router refuses unredacted input" so the test cannot pass by accident?
- Which acceptance criteria need a browser, and which can be integration tests?

## Related cards
[pii-redaction](pii-redaction.md) · [ci-cd-and-deployment](ci-cd-and-deployment.md) · [synthetic-data](synthetic-data.md)

## Notes from the build
### Stage 1 — 2026-10-10

**What was built**
- 31 tests: 28 unit ([tests/unit/](../tests/unit/)) and 3 integration ([tests/integration/](../tests/integration/)).
- [tests/conftest.py](../tests/conftest.py) provides `settings`, `engine` and `session`
  fixtures. The engine fixture creates all tables before a test and drops them after, so
  tests cannot affect each other.
- One test suite, two databases: in-memory SQLite by default (no services needed, under a
  second), PostgreSQL when `TEST_DATABASE_URL` is set, which CI does.

**Decisions and why**
- The unit/integration line here: a unit test touches no database and no app; an
  integration test starts the real app or uses a real session.
- The BSN tests **derive** a valid number from eight digits instead of hardcoding one, so
  no test file contains a number that might belong to someone.
- Three kinds of test already present, worth telling apart:
  - behaviour — `test_keeps_a_valid_incoming_id`
  - failure mode — `test_missing_database_url_fails_at_startup`
  - property of generated data — `test_pii_labels_point_at_the_exact_text` checks 200 emails, not one example.

**Seen in practice**
- `test_replaces_an_incoming_value_that_is_not_a_uuid` is the first small security test:
  it sends a header with a newline in it and checks it never reaches the logs.
- `engine` drops every table. Pointing `TEST_DATABASE_URL` at the development database
  would empty it.

**Try it**
- Change one number in `_BSN_WEIGHTS` in [app/privacy/checksums.py](../app/privacy/checksums.py),
  run `uv run pytest tests/unit -q`, and see *which* tests fail — more than the checksum tests. Undo.
- `uv run pytest tests/unit/test_correlation.py -v` to read the test names as a specification.

**Not met yet**
- No bypass tests: there is no rule to bypass yet. The first is "the router refuses unredacted input" (Stage 2).
- No LLM mock, no Playwright, no recall gate.
- Coverage is not measured; that needs `pytest-cov`, a dependency to approve.
