# Multi-agent orchestration

**Status:** Stage 4 notes added, questions open · **Stages:** 2, 4 · **Requirements:** FR-2, FR-3, FR-4, FR-6–FR-11 · **Code:** `app/agents/`, `app/orchestrator/`

## In one sentence
Six small, single-purpose agents each do one job on an email, and an orchestrator decides
the order, runs some in parallel, retries failures and keeps the email's status up to date.

## Why it matters in this app
Splitting the work makes each step testable, measurable and replaceable. It also makes
the order a safety property: privacy first, human last.

## Core ideas
- **Agent (in this project)** — a stateless step with one interface:
  `async def run(ctx: EmailContext) -> AgentResult`. Not every agent calls an LLM: the
  privacy agent uses Presidio, the case agent queries a database.
- **Orchestration vs choreography** — a central coordinator that knows the workflow,
  versus agents reacting to each other's events with no one in charge. This project uses
  an orchestrator driven by events.
- **Workflow as a graph** — privacy → intake → (policy ‖ case) → drafting → quality → human review.
- **Fan-out / fan-in** — start policy and case together, continue when both are done (FR-9).
- **Retry with a limit** — transient failures are retried; after the limit the email is
  flagged "needs attention" (FR-3).
- **Statelessness** — all state lives in the database or the event payload, so any step
  can be re-run and any worker can pick it up.
- **Status state machine** — received → processing → draft ready → approved / rejected,
  with "needs attention" as the failure exit (FR-4).

## In this project
| Agent | Job | Uses an LLM? |
| --- | --- | --- |
| Privacy | Detect and replace PII | No |
| Intake | Topic, urgency, sensitive flag | Yes |
| Policy | Retrieve relevant rule chunks | No (embeddings) |
| Case | Earlier contacts and open cases | No |
| Drafting | One call: email + sources + case → Dutch draft | Yes |
| Quality | Tone, completeness, unsupported claims | Yes |

Each agent records a duration histogram and a failure counter.

## Questions to be able to answer
- Why must the privacy agent run before the intake agent?
- What does the case agent need that the redacted email no longer contains, and how does it get it safely?
- If the policy agent fails and the case agent succeeds, what is retried?
- What is the difference between this pipeline and an "autonomous agent" that picks its own tools?

## Related cards
[event-driven-architecture](event-driven-architecture.md) · [async-python-fastapi](async-python-fastapi.md) · [inference-routing](inference-routing.md)

## Notes from the build
### Stage 2 — 2026-10-10

**What was built**
- [app/agents/base.py](../app/agents/base.py): the common shape. `EmailContext` (email ID,
  correlation ID, database session) goes in, `AgentResult` (model used, left local) comes out.
- Two agents: [privacy](../app/agents/privacy.py) and [drafting](../app/agents/drafting.py).
- [app/orchestrator/workflow.py](../app/orchestrator/workflow.py): the `Orchestrator`.
- [app/db/status.py](../app/db/status.py): the allowed status changes, as a table.
- [app/services.py](../app/services.py): where the agents are created and handed to the
  orchestrator, in order.

**Decisions and why**
- The orchestrator gets an ordered list of agents and derives the wiring: the first is
  triggered by `email.received`, each next one by the previous agent's `*.completed`.
  Adding an agent is one line in `services.py`.
- **One step = one transaction.** The agent's results, the status change and the audit
  event are committed together; only then is the next event published. A crash mid-step
  leaves nothing half-done.
- Agents add and flush, the orchestrator commits. An agent cannot save its work without
  the audit event that describes it.
- Agents keep no state between emails. What they are given at start-up (the redactor, the
  router) are tools, not memory.
- On any exception: roll back, mark the email "needs attention", write a `*.failed` audit event.
- `transition()` refuses a status change that is not in the table, for example
  received → approved.

**Seen in practice**
- Timings from the first live email: privacy 129 ms, drafting 27,653 ms. The model is
  where the time goes; that is why the drafting step must not block anything else.
- Audit trail of one email: `email.received` → `privacy.completed` →
  `inference.completed` → `drafting.completed`.

**Try it**
- Swap the two agents in `services.py`, fetch an email, see what happens and why. Undo.
- `uv run pytest tests/integration/test_pipeline.py -v`

**Not met yet (Stage 4)**
- Intake, policy, case and quality agents; policy ‖ case in parallel; retries with a limit.
- Per-agent Prometheus histogram and failure counter (Stage 6); for now a log line with `duration_ms`.

### Stage 4 — 2026-10-11 (all six agents, in parallel, with retries)

**What was built**
- Four new agents: [intake](../app/agents/intake.py), [policy](../app/agents/policy.py),
  [case](../app/agents/case.py) and [quality](../app/agents/quality.py).
- [app/orchestrator/workflow.py](../app/orchestrator/workflow.py) now takes **steps**
  instead of agents. A step is one agent, or a tuple of agents that run together:

  ```python
  steps = [
      PrivacyAgent(...),
      IntakeAgent(...),
      (PolicyAgent(...), CaseAgent(...)),  # at the same time
      DraftingAgent(...),
      QualityAgent(...),
  ]
  ```
- Three new columns on `Email` carry results from one agent to the next: `sensitive`,
  `policy_context` and `case_context`.

**Decisions and why**
- **Fan-out / fan-in with `asyncio.gather`.** Policy and case each get their own database
  transaction and their own retries. The step is complete when both are; then one event,
  `policy+case.completed`, triggers drafting.
- **Agents pass results through the database, not to each other.** The drafting agent
  reads `email.policy_context`; it does not know a policy agent exists. That is what
  keeps agents stateless and re-runnable.
- **Retries with backoff.** A failed agent is tried again up to `MAX_STEP_RETRIES` (2)
  times, waiting 2 s and then 4 s. Each retry writes a `*.retried` audit event. After
  the last one: `*.failed` and status "needs attention".
- **A refusal is not retried.** `UnredactedInputError` is deterministic: the same input
  would be refused again, so it fails at once with `inference.refused`.
- **The intake agent routes.** `TEAM_BY_TOPIC` maps a topic to a team. A topic that is
  not in the map leaves the email with the team that has it, for a team lead to route.
- **The case agent finds the sender by `Email.citizen_id`**, set from the sender's address
  when the email arrived. The redacted text no longer says who wrote it.
- **Case summaries are redacted with the email's own mapping** before they are stored.
  They are free text from the case system and can name anyone. The sender keeps the
  placeholder they already have in the email; a new person gets the next number.
- **Parallel can be switched off** (`PARALLEL_AGENTS`). SQLite allows one writer at a
  time, so tests on SQLite run the two agents one after the other.

**Seen in practice**
- Audit trail of one email now: `email.received` → `privacy.completed` →
  `inference.completed` → `intake.completed` → `case.completed` → `policy.completed` →
  `inference.completed` → `drafting.completed` → `inference.completed` → `quality.completed`.
  Case and policy can appear in either order.
- A real run with the small model `gemma3:1b` took 18.5 s. The quality agent failed three
  times on invalid output (`quality.retried`, `quality.retried`, `quality.failed`) and
  the email went to "needs attention" — FR-3 working as designed, on a real failure.
- That same run exposed a design choice worth questioning: a draft existed, and the
  failed *check* of it still sent the whole email to "needs attention".
- A run that was killed halfway left an email in "processing" for good. The event that
  would have continued it lived only in memory.

**Try it**
- `uv run pytest tests/integration/test_agents.py tests/integration/test_pipeline.py -v`
- In `services.py`, move `IntakeAgent` above `PrivacyAgent`, run the tests, read which
  one fails and why. Undo.

**Still open**
- No recovery for emails stuck in "processing" after a crash (Stage 5, Redis Streams).
- ~~Should a failing quality check discard a usable draft?~~ Decided the same day: no.
  The model check is advisory. `EmailContext.final_attempt` tells an agent that a failure
  now would not be retried; the quality agent then keeps the draft, runs its rule checks,
  adds a `not_checked` warning and writes a `quality.model_check_failed` audit event. A
  refusal by the router is never downgraded this way.
- Per-agent Prometheus metrics (Stage 6).
