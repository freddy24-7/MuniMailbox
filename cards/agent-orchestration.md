# Multi-agent orchestration

**Status:** Overview · **Stages:** 2, 4 · **Requirements:** FR-2, FR-3, FR-4, FR-6–FR-11 · **Code:** `app/agents/`, `app/orchestrator/`

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
_Empty — filled in during Stage 2._
