# Hybrid local/cloud inference routing

**Status:** Overview · **Stages:** 2, 4 · **Requirements:** FR-13, FR-14 · **Code:** `app/inference/router.py`, `app/inference/`

## In one sentence
Every model call in the app goes through one router that decides *which* model answers
and refuses to call any model with text that has no redaction record.

## Why it matters in this app
The router is where "privacy by architecture" becomes real. Agents cannot leak PII to the
cloud because they have no way to reach a model except through a component that checks
first. It is also where cost, latency and model choice are decided and recorded.

## Core ideas
- **Chokepoint (guard) pattern** — one narrow door that every call must pass; the rule is
  enforced there once instead of remembered everywhere.
- **Local inference** — a small open-weight model served on your own hardware. vLLM is a
  high-throughput server for NVIDIA GPUs; Ollama runs on machines without one.
- **OpenAI-compatible API** — vLLM, Ollama and most cloud providers speak the same HTTP
  format, so one client class can talk to all of them.
- **Routing policy** — routine requests local; over a size or complexity threshold, or
  after a local failure, cloud (FR-13).
- **Fallback** — a local error or timeout triggers a second attempt elsewhere.
- **Tokens and context window** — the unit of size, cost and limits for a model.
- **Typed input** — the router accepts a "redacted text" type, not a plain string, so
  passing raw text is a type error as well as a runtime refusal.

## In this project
- Never call a model client directly from an agent (CLAUDE.md rule 1).
- Each call records model used, tokens, duration and whether data left the local environment.
- Cloud route is optional and off by default.
- Acceptance test: force a local failure, see the request go to cloud, see the audit log
  show it was anonymized.
- Alert: any cloud call without a redaction record (target: 0).

## Questions to be able to answer
- What could "complexity" mean in practice, and how would the router measure it?
- Why enforce the redaction check in the router rather than in each agent?
- What should happen when both local and cloud fail?
- What is different about running vLLM compared to Ollama, and when does it matter?
- How do you prove with a test that no code path skips the router?

## Related cards
[pii-redaction](pii-redaction.md) · [audit-trail-hash-chain](audit-trail-hash-chain.md) · [grounded-drafting-and-quality](grounded-drafting-and-quality.md) · [observability](observability.md)

## Notes from the build
_Empty — filled in during Stage 2._
