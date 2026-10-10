# Concept cards

One card per important concept in the Municipal Inbox Assistant. The cards are a learning
tool: each starts as a short overview and is deepened during the stage where the concept
is actually built.

## How to use a card

1. **Before a stage** — read the overview and try the "Questions to be able to answer".
   Not being able to answer them yet is the point.
2. **During the stage** — add to "Notes from the build": what was decided, what surprised
   you, what broke, a snippet worth remembering.
3. **After the stage** — answer the questions in your own words and change the status
   from `Overview` to `Deepened`.

Every card has the same sections: in one sentence, why it matters here, core ideas, in
this project, questions to be able to answer, related cards, notes from the build.

## Index

| Card | Concept | First built | Deepened |
| --- | --- | --- | --- |
| [async-python-fastapi](async-python-fastapi.md) | Async Python and FastAPI | Stage 1 | Stage 2 |
| [config-and-secrets](config-and-secrets.md) | Configuration and secrets | Stage 1 | Stage 6 |
| [data-model-and-migrations](data-model-and-migrations.md) | Data model, SQLAlchemy and Alembic | Stage 1 | Stage 3 |
| [synthetic-data](synthetic-data.md) | Synthetic data | Stage 1 | Stage 3 |
| [testing-strategy](testing-strategy.md) | Testing strategy and quality gates | Stage 1 | every stage |
| [pii-redaction](pii-redaction.md) | PII detection and redaction | Stage 2 | Stage 3 |
| [inference-routing](inference-routing.md) | Hybrid local/cloud inference routing | Stage 2 | Stage 4 |
| [event-driven-architecture](event-driven-architecture.md) | Events and the event bus | Stage 2 | Stage 5 |
| [agent-orchestration](agent-orchestration.md) | Multi-agent orchestration | Stage 2 | Stage 4 |
| [human-in-the-loop-review](human-in-the-loop-review.md) | Human review and the review UI | Stage 2 | Stage 5 |
| [audit-trail-hash-chain](audit-trail-hash-chain.md) | Tamper-evident audit trail | Stage 2 | Stage 3 |
| [encryption-and-retention](encryption-and-retention.md) | Encryption at rest, restoration, retention | Stage 3 | Stage 3 |
| [rbac](rbac.md) | Role-based access control | Stage 3 | Stage 5 |
| [rag-ingestion-and-embeddings](rag-ingestion-and-embeddings.md) | RAG: ingestion, chunking, embeddings | Stage 4 | Stage 4 |
| [hybrid-retrieval](hybrid-retrieval.md) | Hybrid retrieval (semantic + keyword) | Stage 4 | Stage 4 |
| [grounded-drafting-and-quality](grounded-drafting-and-quality.md) | Grounded drafting, prompts, quality checks | Stage 4 | Stage 4 |
| [observability](observability.md) | Metrics, logs, dashboards, alerts | Stage 1 (logs) | Stage 6 |
| [ci-cd-and-deployment](ci-cd-and-deployment.md) | CI/CD, Docker Compose, systemd | Stage 1 (CI) | Stage 6 |

## Stages (short version)

1. Foundations
2. Thin end-to-end slice — **gate 1**
3. Privacy and governance hardening
4. Knowledge base and the full agent set
5. Full review interface and Redis Streams
6. Observability, end-to-end tests and deployment — **gate 2 (MVP)**
