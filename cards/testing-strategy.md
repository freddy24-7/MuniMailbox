# Testing strategy and quality gates

**Status:** Overview · **Stages:** all · **Requirements:** GR-4, NFR maintainability · **Code:** `tests/unit`, `tests/integration`, `tests/e2e`, `tests/pii_testset`

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
_Empty — filled in from Stage 1 onwards._
