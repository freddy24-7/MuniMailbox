# CLAUDE.md — Municipal Inbox Assistant

## What this project is
A demo app that drafts replies to citizen emails for a **fictional** Dutch municipality.
A pipeline of agents classifies each email, redacts personal data, retrieves relevant
municipal rules (RAG), checks case history, and drafts a sourced reply. A human civil
servant always reviews and approves before anything is "sent" (outbox is simulated).

Full requirements: see `docs/PRD.md` (requirement IDs like FR-5, GR-3, NFR-1 are used in
code comments, tests and commit messages).

## Non-negotiable rules
1. **No PII reaches any LLM unredacted.** Every model call (local or cloud) goes through
   `app/inference/router.py`, which must refuse input without a redaction record (FR-14).
   Never call a model client directly from an agent.
2. **Only synthetic data.** Never add real names, BSNs, addresses or emails to code,
   fixtures, tests, prompts or docs. Use `scripts/generate_synthetic.py` (Faker, `nl_NL`).
3. **Humans approve every reply.** No code path may move a draft to the outbox without a
   `Review` with action `approve` by a user with the caseworker role.
4. **Audit everything.** Every pipeline step and human action writes an `AuditEvent`
   (append-only, hash-chained). Never update or delete audit rows.
5. **Restore PII only in the backend**, only for roles allowed to see it. Auditors never
   receive restored values.
6. Secrets live in `.env` (never committed). `.env.example` lists every variable.

## Tech stack
- Python 3.12, FastAPI, async-first (`async def` for all I/O; no blocking calls in handlers)
- Pydantic v2 for schemas, SQLAlchemy 2.x async + Alembic, PostgreSQL (SQLite allowed for local dev)
- Events: Redis Streams (`app/events/`); in-process async queue behind the same interface for tests
- Local inference: vLLM (OpenAI-compatible API); Ollama as fallback on machines without an NVIDIA GPU
- Cloud inference: OpenAI-compatible API, used only via the router after redaction
- PII: Microsoft Presidio + custom Dutch recognizers (BSN with 11-check, postcode, IBAN, NL phone)
- RAG: ChromaDB, two embedding collections (semantic + keyword/BM25), merged results
- Frontend: server-rendered Jinja2 templates + HTMX (unless decided otherwise in the PRD)
- Observability: `prometheus-client`, structured JSON logs (structlog) shipped to Loki, Grafana
- Tests: pytest, pytest-asyncio, Playwright for end-to-end
- Runtime: Docker Compose locally; systemd services on the VPS; GitHub Actions for CI/CD

## Project layout
```
app/
  main.py              # FastAPI app factory
  api/                 # routers; every endpoint declares required role
  agents/              # one module per agent: intake, privacy, policy, case, drafting, quality
  orchestrator/        # subscribes to events, runs workflow, retries, status updates
  events/              # event bus interface + Redis and in-memory implementations
  inference/           # router.py (local vs cloud), clients, prompt templates
  privacy/             # Presidio setup, Dutch recognizers, redact/restore, mapping store
  rag/                 # ingestion, chunking, embeddings, retrieval
  db/                  # models, session, repositories
  audit/               # AuditEvent writer + hash-chain verification
  auth/                # demo login, RBAC permissions (single source of truth)
  observability/       # metrics, logging config, correlation-ID middleware
  web/                 # templates and static files for the review UI
prompts/               # prompt templates (Dutch), versioned
scripts/               # generate_synthetic.py, ingest_documents.py, verify_audit.py
tests/
  unit/  integration/  e2e/  pii_testset/
deploy/                # docker-compose.yml, systemd units, Grafana dashboards, Prometheus config
docs/                  # PRD.md, architecture notes, decisions/
```

## Commands
```bash
docker compose up -d                 # full stack: app, postgres, redis, chromadb, prometheus, loki, grafana
uv sync                              # install dependencies
uv run uvicorn app.main:app --reload # run API locally
uv run alembic upgrade head          # apply migrations
uv run python scripts/generate_synthetic.py --citizens 200 --emails 500
uv run python scripts/ingest_documents.py data/documents/
uv run pytest tests/unit tests/integration
uv run pytest tests/pii_testset      # PII recall gate (must stay >= threshold in PRD)
uv run pytest tests/e2e              # Playwright end-to-end
uv run ruff check . && uv run ruff format . && uv run mypy app
```

## Coding conventions
- Type hints everywhere; `mypy --strict` on `app/`. Ruff for lint and format.
- Agents implement a common interface: `async def run(ctx: EmailContext) -> AgentResult`.
  Agents are stateless; all state goes through the database or the event payload.
- Every event and log line carries the email's `correlation_id`.
- Each agent records a Prometheus histogram for duration and a counter for failures.
- Prompts live in `prompts/`, not inline in code. Draft replies are in Dutch.
- Drafts must include source references from retrieval; never let the model invent sources.
- Config through `pydantic-settings`; no hardcoded URLs, model names or thresholds.
- Small, focused commits; reference requirement IDs (e.g. `feat(privacy): add IBAN recognizer (FR-5)`).

## Testing expectations
- New agent or endpoint → unit tests plus at least one integration test.
- Anything touching privacy, RBAC or audit → add a test that proves the rule cannot be bypassed.
- LLM calls are mocked in unit and integration tests; e2e tests may use a small local model.
- Keep the PII test set growing: when a redaction miss is found, add the case first, then fix.

## Working style for Claude
- Build in thin vertical slices; keep the end-to-end path working after every change.
- Read the relevant PRD section before implementing a feature.
- Ask before adding a new dependency or changing the architecture.
- When unsure whether something could leak personal data, stop and ask.
- Do not edit files in `deploy/` or migrations that have already been applied without saying so.
