# Hybrid local/cloud inference routing

**Status:** Stage 2 notes added, questions open · **Stages:** 2, 4 · **Requirements:** FR-13, FR-14 · **Code:** `app/inference/router.py`, `app/inference/`

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
### Stage 2 — 2026-10-10

**What was built**
- [app/inference/router.py](../app/inference/router.py): `InferenceRouter.complete()`, the
  only way to reach a model.
- [app/inference/clients.py](../app/inference/clients.py): one small HTTP client for the
  OpenAI-compatible `/chat/completions` format. Ollama speaks it at `localhost:11434/v1`.
- [app/inference/prompts.py](../app/inference/prompts.py): loads a template from `prompts/`
  and fills in the variables.
- Local model: `gemma3:4b` through Ollama. Only the local route exists so far.

**Decisions and why**
- Agents hand the router a **prompt name and variables**, not a finished prompt. The
  router then knows exactly which text came from the email and can check it.
- Two checks before any call, in `_require_redacted`:
  1. a `RedactionRecord` exists for the email;
  2. none of the values the privacy agent replaced appears in the variables.
  The second catches a caller that has a record but passes the original text anyway.
- Variables have the type `RedactedText`. A plain `str` is rejected by mypy before the
  code ever runs; the runtime checks catch what the type cannot.
- A refusal raises `UnredactedInputError`, whose message never contains the text.
- Every successful call writes an audit event with the model name and `left_local`.

**Seen in practice**
- First draft: 27.5 s, most of it loading the model into memory; 452 prompt tokens, 118
  completion tokens. The log line `inference.completed` carries all of that.
- [tests/unit/test_architecture.py](../tests/unit/test_architecture.py) reads the imports
  of every file and fails if anything but the router and the startup wiring imports the client.
- `test_a_workflow_that_skips_the_privacy_agent_never_reaches_the_model` builds the
  pipeline wrongly on purpose: the model is never called and the audit log says `inference.refused`.

**Try it**
- Stop Ollama, fetch an email, and watch it go to "needs attention". Check the audit page.
- In [app/agents/drafting.py](../app/agents/drafting.py), try passing a plain string as a
  variable and run `uv run mypy app`. Undo.

**Not met yet (Stage 4)**
- The cloud route, the size/complexity rule, fallback after a local failure, `left_local=True`.
- Check 2 only knows about values that were *detected*. It cannot notice PII the redactor missed.
