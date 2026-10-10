# Multi-agent orchestration

**Status:** Stage 2 notes added, questions open · **Stages:** 2, 4 · **Requirements:** FR-2, FR-3, FR-4, FR-6–FR-11 · **Code:** `app/agents/`, `app/orchestrator/`

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
