# Municipal Inbox Assistant — Product Requirements Document

Oct 10, 2026 · @Freddy

## Overview

The Municipal Inbox Assistant drafts replies to citizen emails for a fictional Dutch municipality, so a civil servant only reviews, edits and approves. It is a single-developer demo project that exercises a production-style AI stack: multi-agent orchestration, hybrid local/cloud inference, RAG, privacy by design and full observability.

**Problem.** Municipal teams receive a steady stream of emails about permits, waste collection, benefits, complaints and address changes. Answering them means finding the right regulation, checking the citizen's case history and writing a careful reply. That work is slow, repetitive and inconsistent between staff, and the emails are full of personal data that must be protected under the GDPR (AVG).

**Goals**

1. Cut the time a civil servant spends per email by drafting a sourced reply they can approve in minutes.
2. Keep every reply grounded in cited municipal documents, so staff can verify it.
3. Never send personal data outside the local environment; anything that goes to a cloud model is anonymized first.
4. Make every step traceable: who did what, which data was used, which model answered.
5. Serve as a portfolio piece that demonstrates each concept in the original tech stack end to end.

**Non-goals**

- Sending replies automatically. A human approves every outgoing message.
- Making legal or administrative decisions on behalf of the municipality.
- Handling real citizen data. The project uses synthetic data only.
- Production-scale serving of many concurrent users on large models.

## Users and roles

Three roles use the system; each sees only what its job needs.

| Role | Who | Main tasks | Can see |
| --- | --- | --- | --- |
| Caseworker | Civil servant handling citizen mail | Review drafts, edit, approve or reject, reassign | Emails assigned to them, full personal data, sources, case history |
| Team lead | Manages a team of caseworkers | Assign work, monitor queue and quality, handle escalations | Whole team's queue, statistics, all drafts |
| Auditor | Privacy officer or internal control | Check that data handling follows the rules | Audit logs and redaction records, no email content in clear text |

**Key user stories**

- As a caseworker, I want each new email to arrive already classified and with a draft reply, so I can start from a proposal instead of a blank page.
- As a caseworker, I want to see which regulations the draft is based on, with links, so I can check the answer before approving it.
- As a caseworker, I want to see the citizen's earlier contacts and open cases, so my reply fits their situation.
- As a caseworker, I want to edit the draft inline and approve it with one click.
- As a team lead, I want to see volume per topic and average handling time, so I can plan capacity.
- As a team lead, I want urgent or sensitive emails flagged and routed to me.
- As an auditor, I want to see, per email, whether data left the local environment and confirm it was anonymized.
- As an auditor, I want a complete, tamper-evident log of who accessed and approved what.

## Scope

The MVP proves the full pipeline for one email at a time; later versions add breadth and polish.

**MVP (must have)**

- Email intake from a local mailbox or a simulated feed of synthetic emails.
- Five topics: permits, waste collection, benefits, complaints, address changes.
- Intake, privacy, policy, case, drafting and quality agents.
- Local model inference, with cloud fallback after anonymization.
- RAG over a curated set of public municipal documents.
- Review interface for caseworkers: queue, email, draft, sources, approve/edit/reject.
- Role-based access for caseworker, team lead and auditor.
- Audit trail of every pipeline step and human action.
- Metrics, logs and dashboards with Prometheus, Loki and Grafana.
- CI with unit and end-to-end tests, automated deployment to a VPS.

**Later (should/could have)**

- Scanned letters as PDF, processed with OCR.
- Replies in English and other languages for non-Dutch-speaking residents.
- Feedback loop: measure how much caseworkers change drafts and use that to improve prompts.
- Team-lead analytics: trends per topic, approval rates, SLA tracking.
- Comparison mode for the two embedding approaches.

**Out of scope**

- Real citizen data or connection to real municipal systems.
- Automatic sending of replies.
- DigiD or other real identity integration; demo login only.
- High availability and multi-server scaling.

## Functional requirements

Each requirement has an ID so tests and tasks can refer to it.

**Intake and orchestration**

- **FR-1** The system ingests new emails (subject, body, sender, timestamp, attachments list) and emits an `email.received` event.
- **FR-2** An orchestrator subscribes to events and starts the agent workflow per email; each step emits its own completion event.
- **FR-3** Failed steps are retried up to a configurable limit, then the email is flagged for manual handling.
- **FR-4** Every email has a status: received, processing, draft ready, approved, rejected, needs attention.

**Agents**

- **FR-5 Privacy agent** detects PII (names, addresses, postcodes, BSN, IBAN, phone, email, dates of birth) and replaces it with typed placeholders such as `[PERSON_1]`. The mapping is stored locally and encrypted.
- **FR-6 Intake agent** classifies the email into one of the five topics plus "other", sets urgency (low, normal, high) and flags sensitive content such as threats or distress.
- **FR-7 Policy agent** retrieves the most relevant chunks from the municipal knowledge base for the email's topic and question.
- **FR-8 Case agent** looks up the sender's earlier contacts and open cases in the mock case database.
- **FR-9** The policy and case agents run in parallel.
- **FR-10 Drafting agent** combines the redacted email, retrieved sources and case context into one LLM call and produces a draft reply in Dutch, with source references.
- **FR-11 Quality agent** checks the draft for tone, completeness, unsupported claims and contradictions with the cited sources, and attaches warnings.
- **FR-12** After drafting, placeholders are replaced with the original values for display to authorized users only.

**Inference routing**

- **FR-13** Routine requests use the local model. Requests above a configurable size or complexity, or after a local failure, go to the cloud model.
- **FR-14** A request may only be sent to the cloud if the privacy agent has processed it; otherwise the router refuses it.

**Knowledge base (RAG)**

- **FR-15** An ingestion script loads public municipal documents (HTML, PDF, text), splits them into chunks and stores embeddings with metadata (title, URL, topic, date) in ChromaDB.
- **FR-16** Retrieval supports two embedding approaches (for example semantic plus keyword/BM25) and merges their results.
- **FR-17** Every draft lists the documents it used, with title and link.

**Review interface**

- **FR-18** Caseworkers see a queue sorted by urgency and age, filterable by topic and status.
- **FR-19** The detail view shows the original email, classification, case history, draft, sources and quality warnings side by side.
- **FR-20** Caseworkers can edit the draft, approve, reject with a reason, or reassign.
- **FR-21** Approved replies are written to an outbox (simulated sending).
- **FR-22** Team leads see a dashboard with volume per topic, average handling time, and the share of drafts approved unchanged.
- **FR-23** Auditors see a searchable audit log per email and per user.

## Privacy, security and governance

Privacy is enforced by the architecture, not by convention: data that has not been redacted cannot reach the cloud.

**PII handling**

- **GR-1** Redaction runs before any LLM call, local or cloud, using Microsoft Presidio plus custom Dutch recognizers (BSN with 11-check, postcode, IBAN, Dutch phone formats).
- **GR-2** The placeholder-to-value mapping is stored per email, encrypted at rest, and deleted after a configurable retention period.
- **GR-3** Restoration happens only in the backend, only for users whose role allows it.
- **GR-4** A redaction test set of synthetic emails with known PII measures recall; the build fails if recall drops below the agreed threshold.

**Access control (RBAC)**

- **GR-5** Every API endpoint checks the user's role; roles and permissions are defined in one place.
- **GR-6** Caseworkers see only emails assigned to them or their team; auditors never see restored personal data.

**Audit trail**

- **GR-7** Every pipeline step and human action is logged with timestamp, actor (agent or user), email ID, action, model used, and whether data left the local environment.
- **GR-8** Audit records are append-only; each record includes a hash of the previous one so tampering is detectable.

**Data and secrets**

- **GR-9** Only synthetic data is used: generated citizens, cases and emails (for example with Faker and LLM-generated email texts).
- **GR-10** API keys and encryption keys live in environment variables or a secrets file outside the repository.
- **GR-11** All traffic to the web interface uses HTTPS; the database and model server are not exposed publicly.

## Non-functional requirements

Targets are sized for a demo on one GPU machine or a rented GPU, not for production load.

| Area | Requirement | Target |
| --- | --- | --- |
| Latency | Time from email received to draft ready | Under 60 s for a typical email on the local model |
| Throughput | Emails processed concurrently | At least 5 without errors |
| Reliability | Pipeline failures | No email lost; failed steps retried and flagged |
| Privacy | PII redaction recall on test set | At least 95% (to be confirmed) |
| Quality | Drafts with at least one valid source reference | 100% |
| Observability | Traceability | Every email traceable through logs by one correlation ID |
| Portability | Local setup | Whole stack starts with one `docker compose up` |
| Maintainability | Test coverage of core modules | At least 70% |

**Observability requirements**

- **NFR-1** Prometheus metrics: emails processed, duration per agent, local vs. cloud calls, tokens used, redactions per email, error rate, queue length.
- **NFR-2** Structured JSON logs shipped to Loki, each tagged with the email's correlation ID.
- **NFR-3** Grafana dashboards for system health, agent performance and privacy events, with alerts on error-rate spikes and on any cloud call without a redaction record.

## System architecture

An event-driven pipeline of six agents turns each email into a reviewed draft, with redaction as the first step and a human as the last.

&#91;embedded content: agent workflow · 8 components, 2 agents in parallel\]

The policy and case agents run at the same time; every model call goes through the inference router, which lets only redacted text reach the cloud.

**Tech stack**

| Layer | Choice | Purpose |
| --- | --- | --- |
| API and backend | FastAPI, Python 3.12, async | Endpoints, orchestration, agents |
| Events | Redis Streams (or an in-process async queue for the MVP) | Event-driven agent triggers |
| Local inference | vLLM on NVIDIA GPU, Ollama as fallback | Small open-weight model, for example a small Qwen MoE |
| Cloud inference | OpenAI-compatible cloud API | Fallback for complex requests |
| PII | Microsoft Presidio + custom Dutch recognizers | Redaction and restoration |
| Vector store | ChromaDB | RAG over municipal documents |
| Database | PostgreSQL (SQLite for minimal setup) | Emails, cases, drafts, audit log |
| Frontend | Server-rendered templates with HTMX, or React | Review interface and dashboards |
| Observability | Prometheus, Loki, Grafana | Metrics, logs, dashboards, alerts |
| CI/CD | GitHub Actions, pytest, Playwright | Unit and end-to-end tests, deployment |
| Runtime | Docker Compose locally, systemd services on a VPS | Running and restarting components |

## Data model

Relational data lives in PostgreSQL (or SQLite for a minimal setup); document embeddings live in ChromaDB.

| Entity | Key fields | Notes |
| --- | --- | --- |
| Citizen | id, name, address, BSN, email, phone | Synthetic only |
| Case | id, citizen\_id, topic, status, opened\_at, summary | Mock case-management history |
| Email | id, citizen\_id, subject, body\_redacted, received\_at, topic, urgency, status, assigned\_to | Original body stored encrypted |
| PiiMapping | email\_id, placeholder, encrypted\_value, entity\_type | Deleted after retention period |
| Draft | id, email\_id, text\_redacted, sources, warnings, model\_used, created\_at | One email can have several drafts |
| Review | id, draft\_id, user\_id, action, edited\_text, reason, timestamp | Approve, edit, reject, reassign |
| User | id, name, role, team | Caseworker, team lead or auditor |
| AuditEvent | id, email\_id, actor, action, model, left\_local, timestamp, prev\_hash | Append-only, hash-chained |
| DocumentChunk (ChromaDB) | id, text, embedding, title, url, topic, published\_at | Two embedding collections |

## Success metrics and acceptance criteria

The MVP is done when a synthetic email travels from inbox to approved reply with every step visible in logs, dashboards and the audit trail.

**Success metrics**

| Metric | Target |
| --- | --- |
| Drafts approved without edits | At least 40% |
| Drafts approved after minor edits | At least 80% combined |
| Correct topic classification on test set | At least 90% |
| PII redaction recall on test set | At least 95% |
| Cloud calls without redaction record | 0 |
| Median time to draft | Under 60 s |

**Acceptance criteria**

- [ ] An end-to-end test sends a synthetic email and receives a draft with at least one source reference.
- [ ] The draft shown to a caseworker contains restored personal data; the stored draft and all LLM inputs contain placeholders only.
- [ ] A forced local-model failure routes the request to the cloud, and the audit log shows it was anonymized.
- [ ] An auditor account cannot view restored personal data; a caseworker cannot view another team's emails.
- [ ] Grafana shows the email's journey by correlation ID, with duration per agent.
- [ ] A push to the main branch runs tests and deploys to the VPS without manual steps.
- [ ] Tampering with an audit record is detected by the hash-chain check.

## Milestones and delivery plan

At 8 to 10 hours a week, one developer can reach a working end-to-end slice in about 4 weeks and the full MVP in about 11.

&#91;embedded content: delivery plan · 5 phases, 2 gates, about 11 weeks\]

The thin-slice gate is the key checkpoint: once one email flows through redaction, a model and the review screen, every later phase adds to working software rather than building parts in isolation.

## Risks, assumptions and open questions

The biggest risk is redaction missing personal data, so it gets its own test set and a build gate.

**Risks**

| Risk | Impact | Mitigation |
| --- | --- | --- |
| PII redaction misses an entity | Personal data could reach the cloud | Recall test set in CI, custom Dutch recognizers, cloud route optional and off by default |
| Small local model writes weak Dutch replies | Low approval rate | Strong prompts with examples, quality agent, cloud fallback for hard cases |
| No suitable GPU available | vLLM cannot run locally | Ollama or llama.cpp locally; rented GPU for vLLM demos |
| Scope creep across 9 stack areas | Project never finishes | Thin end-to-end slice first, MVP list fixed |
| Hallucinated rules in drafts | Wrong information to citizens | Mandatory sources, quality agent checks claims against them, human approval |
| Public documents change or disappear | Stale knowledge base | Store fetch date per document, re-ingest script |

**Assumptions**

- One developer working part-time, roughly 8–10 hours a week.
- Access to either an NVIDIA GPU with at least 16 GB VRAM or a budget for a few hours of rented GPU time.
- A small budget for cloud model API calls during testing.
- Public documents from Dutch municipalities may be used for a non-commercial demo.

**Open questions**

- Which real municipality's public documents to use as the knowledge base, or a mix of several?
- Which cloud model provider to use for the fallback route?
- PostgreSQL from the start, or SQLite first and migrate later?
- Should the review interface be a simple server-rendered app or a separate frontend (for example React)?
- What PII recall threshold is acceptable for the demo?
