# Hybrid local/cloud inference routing

**Status:** Stage 4 notes added, questions open · **Stages:** 2, 4 · **Requirements:** FR-13, FR-14 · **Code:** `app/inference/router.py`, `app/inference/`

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

### Stage 4 — 2026-10-11 (cloud route, structured output, embeddings, a concurrency limit)

**What was built**
- The cloud route in [app/inference/router.py](../app/inference/router.py), off unless
  `CLOUD_LLM_ENABLED=true`. `.env.example` points it at Mistral (EU, same API format).
- `complete_json()`: the answer is validated against a Pydantic schema.
- `embed_query()` for email text and `embed_public()` for documents.
- A limit on simultaneous calls to the local server (`LOCAL_LLM_MAX_CONCURRENCY`, default 1).

**Decisions and why**
- **The routing rule is two lines.** A request goes to the cloud when the email text is
  longer than `CLOUD_ROUTE_MIN_CHARS` (6000), or when the local model fails. "Complexity"
  is measured as size, because size is the one thing known before the call.
- **One check guards both routes.** `_require_redacted` runs before the router decides
  where the request goes. There is no path on which unredacted text is acceptable.
- **An answer that does not fit the schema counts as a failure of that model**, like a
  timeout. With a cloud model configured it falls back; without one the agent is retried.
- **Errors never quote the model.** `InvalidModelOutputError` carries a count of
  validation errors, not the text, because error messages end up in logs.
- **A fallback is audited twice**: `inference.local_failed`, then `inference.completed`
  with the cloud model's name and `left_local=True`.
- **Embedding email text is guarded like a chat call.** An embedding model is still a
  model; the query is the redacted email.
- **Chat and embedding calls share one semaphore.** They go to the same machine. Five
  emails can be in the pipeline at once, but only one call reaches Ollama at a time.
  Cloud calls are not limited.

**Seen in practice**
- This laptop has 8 GB of RAM. `gemma3:4b` (4–5 GB) plus the embedding model, the Docker
  VM and spaCy did not fit: the machine swapped until the session died. The cause was
  memory; it looked like CPU.
- `gemma3:1b` fits (lowest point: 25% of memory free) and is not good enough. Its draft
  was one line, "Geachte heer/mevrouw,", and its quality report left out a required field
  three times. It also marked a bin complaint as sensitive.
- So the local/cloud question stopped being theoretical. The reasons to leave the machine
  are not only "the request is large" but also "the hardware cannot hold a model that is
  good enough".
- The cloud route has only been exercised with fake models so far, never a real provider.

**Try it**
- Read the four cloud tests in [tests/integration/test_router.py](../tests/integration/test_router.py),
  especially `test_the_cloud_route_refuses_unredacted_text_exactly_like_the_local_one`.
- Set `LOCAL_LLM_MAX_CONCURRENCY=2` in a test and see which assertion changes.

**Still open**
- Which model for real runs on this machine: Mistral through the cloud route, or
  `gemma3:4b` with everything else closed.
- The fallback only triggers on an error or invalid output. A valid but poor answer
  (the one-line draft) stays local.
- What happens when local and cloud both fail: today the agent is retried, then the email
  goes to "needs attention". No alert yet (Stage 6).
