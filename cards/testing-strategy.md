# Testing strategy and quality gates

**Status:** Stage 3 notes added, questions open · **Stages:** all · **Requirements:** GR-4, NFR maintainability · **Code:** `tests/unit`, `tests/integration`, `tests/e2e`, `tests/pii_testset`

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

### Stage 2 and 3 — 2026-10-10

**Where the suite stands**
- 122 tests on PostgreSQL, 118 on SQLite (four are PostgreSQL-only), plus the recall
  gate. LLM calls are faked everywhere.

**Kinds of test that arrived**
- **Bypass tests** — build the system wrongly on purpose and check the rule still holds.
  `test_a_workflow_that_skips_the_privacy_agent_never_reaches_the_model` leaves the
  privacy agent out; the "outsider" in [test_scope.py](../tests/integration/test_scope.py)
  has every permission and the wrong team.
- **Architecture tests** — [test_architecture.py](../tests/unit/test_architecture.py)
  reads the source: who imports the model client, where `OutboxMessage(` is written,
  which endpoints lack a login check. They fail on a change that no behavioural test notices.
- **A metric gate** — [test_recall.py](../tests/pii_testset/test_recall.py) computes a
  number and compares it with a threshold from the settings.
- **Precision tests** — sentences that must come through unchanged. Without them, a
  redactor that replaces everything would pass the recall gate.

**Decisions and why**
- `FakeLLM` records every prompt it receives. The assertion "no personal value reached
  the model" is made on exactly what a real model would have been sent.
- The test set is **generated at test time** from a seed, not stored as a file. No file
  with 300 valid-looking BSNs is committed.
- Time is a parameter (`purge_expired(now=...)`), so retention is tested without waiting.
- "Add the case first, then fix": a miss becomes a template in `hard_cases.py`.

**Seen in practice**
- One test failed 5 runs out of 12 on SQLite and never on PostgreSQL. The cause was a
  real race on the audit chain that only PostgreSQL's lock prevents. An intermittent
  failure was a correct finding, not noise; the fix was to say so in the test, not to retry.
- A new FastAPI version changed how included routers are stored and broke the
  "every endpoint needs a login" test. That test asserts equality with the list of
  public paths, so it failed loudly instead of silently finding nothing.

**Still open**
- Coverage is still not measured. No end-to-end browser test yet (Stage 6).

### Stage 4 — 2026-10-11

**Kinds of test that arrived**
- **Fakes that answer per prompt.** `FakeLLM` now replies in each agent's own format
  (classification, draft with sources, quality report). A test steers the "model" by
  setting an attribute: `fake_llm.sources_used = [1, 7]`.
- **A fake embedder and an in-memory vector store**, behind the same interfaces as Ollama
  and ChromaDB. The whole pipeline runs in tests with no model and no container.
- **The bypass test, per agent and per route.** Each agent that reaches a model is run
  without a redaction record and must be refused; the cloud route gets the same test as
  the local one, with and without a failing local model.
- **A concurrency test.** Slow fakes count how many calls are inside a model at once; the
  maximum must equal the configured limit.

**Decisions and why**
- Retrieval tests use hand-made two-number vectors (`[1.0, 0.0]`). What is tested is the
  merging and the floor, not the embedding model.
- Tests set `retry_backoff_seconds=0`: the retry logic is tested, the waiting is not.
- On SQLite the two parallel agents run one after the other. The parallel path is only
  exercised on PostgreSQL (CI, or a throwaway local database).

**Seen in practice**
- 164 tests pass on SQLite in about 13 seconds.
- Writing a test for "a number with different spacing is the same number" found a real
  bug in the quality rules (see [grounded-drafting-and-quality](grounded-drafting-and-quality.md)).
- A made-up surname in a test, "Voorbeeld", was not redacted: it is a common noun.
  Realistic surnames were. A test name is test data too.
- The mocked tests could not show that `gemma3:1b` is too weak. One real email could.
  Fakes prove the plumbing; only a real model shows the quality.
- On this 8 GB machine, tests, type check and a model run are done one after another.

**Still open**
- No retrieval test set, no prompt evaluation set. Both are measurements this stage needs
  before its choices (chunk size, similarity floor, prompt v2) can be called good.
- The surname miss is not yet a case in `hard_cases.py`.

### Stage 6 — 2026-10-11 (browser tests, coverage, and a lesson from CI)

**Kinds of test that arrived**
- **Browser tests** ([tests/e2e/](../tests/e2e/)): Playwright drives a real browser
  against the real app in its own process. Real HTTP, real database, real redaction and
  pipeline; only the models and the vector store are fakes. Three journeys: an email from
  inbox to approved reply, the auditor who cannot see the person, the team lead who can
  reassign but not approve.
- **A coverage gate:** CI fails below 70% (the PRD's target); it measures about 91%.
- **Tests against a real Redis,** skipped when none is configured.

**Decisions and why**
- The browser tests wait for what a user would see ("draft ready" appears in the queue),
  never for a number of seconds.
- They assert the PRD's acceptance criteria literally: at least one source link, restored
  data for the caseworker, placeholders in what is stored.
- Coverage needed one setting to be honest: without `concurrency = ["greenlet"]`, lines
  after a database `await` were counted as never run.

**Seen in practice**
- The test pyramid as it stands: about 150 unit tests, about 85 integration tests, 3
  browser tests. The browser tests take as long as all the others together.
- A test that passed 100% locally failed about half the time in CI. It depended on two
  emails finishing in order, which only SQLite guarantees. Running the same tests four
  times on PostgreSQL after the fix was the proof, not one green run.
- Measuring was repeatedly what changed the picture: redaction "99.8%" hid the towns,
  "hybrid search" was not better than semantic, the small model's drafts were unusable.

**Still open**
- No test set for classification accuracy (the PRD asks for 90%) or for draft quality.
- The browser tests run the fake model; no automated test covers a real model's output.
