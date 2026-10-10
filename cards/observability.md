# Observability: metrics, logs, dashboards, alerts

**Status:** Stage 1 notes added, questions open · **Stages:** 1 (logs, correlation ID), 6 · **Requirements:** NFR-1, NFR-2, NFR-3 · **Code:** `app/observability/`, `deploy/`

## In one sentence
The running system continuously reports what it is doing, in numbers and in structured
log lines, so that any question about one email or about overall health can be answered
from outside without attaching a debugger.

## Why it matters in this app
A pipeline of six agents and two model backends fails in ways a single stack trace does
not explain. And one privacy rule is itself monitored: a cloud call without a redaction
record must raise an alert.

## Core ideas
- **Monitoring vs observability** — watching known failure signs versus being able to ask
  new questions of the system.
- **Metrics** — cheap numbers over time. **Counter** (only goes up: emails processed),
  **gauge** (goes up and down: queue length), **histogram** (distribution: duration per agent).
- **Labels and cardinality** — labels split a metric (`agent="drafting"`). Never use an
  unbounded value such as an email ID as a label.
- **Pull model** — Prometheus scrapes a `/metrics` endpoint on a schedule.
- **Structured logs** — each line is JSON with fields, not a sentence (structlog).
- **Correlation ID** — one ID per email on every log line and event, so its whole journey
  can be found with one query.
- **Loki** — stores logs, indexed by labels; queried with LogQL.
- **Grafana** — dashboards over Prometheus and Loki, plus alert rules.
- **Percentiles** — p50 and p95 say more about latency than an average.
- **Logs are a leak path** — a log line with an email body bypasses every other privacy control.

## In this project
- Metrics (NFR-1): emails processed, duration per agent, local vs cloud calls, tokens
  used, redactions per email, error rate, queue length.
- Dashboards (NFR-3): system health, agent performance, privacy events.
- Alerts: error-rate spike; any cloud call without a redaction record.
- Acceptance criterion: Grafana shows an email's journey by correlation ID with duration per agent.

## Questions to be able to answer
- Which question is answered by a metric, which by a log, and which by the audit trail?
- Why is the correlation ID a log field but not a metric label?
- How is "median time to draft under 60 s" computed from a histogram?
- How do you make sure no PII ends up in a log line?
- What is the difference between the audit trail and the logs in Loki?

## Related cards
[audit-trail-hash-chain](audit-trail-hash-chain.md) · [event-driven-architecture](event-driven-architecture.md) · [inference-routing](inference-routing.md) · [ci-cd-and-deployment](ci-cd-and-deployment.md)

## Notes from the build
### Stage 1 — 2026-10-10 (logs and correlation ID only)

**What was built**
- [app/observability/logging.py](../app/observability/logging.py): structlog configured as
  a chain of processors. Each adds one thing to the event — bound context, level,
  timestamp — and the last one renders it as JSON (or coloured text when `LOG_FORMAT=console`).
- [app/observability/correlation.py](../app/observability/correlation.py): middleware that
  picks a correlation ID per request, binds it to the logging context, returns it in the
  `X-Correlation-ID` response header, and writes one `http.request` line per request.

A real line from the running app:

```json
{"method": "GET", "path": "/healthz", "status": 200, "duration_ms": 2.0,
 "event": "http.request", "correlation_id": "1130c7a4-829d-4547-b61b-90971f613b39",
 "level": "info", "timestamp": "2026-10-10T15:59:24.192276Z"}
```

**Decisions and why**
- The ID is bound with `structlog.contextvars`, not passed as an argument. Any code that
  logs during the request gets the ID automatically, and two concurrent requests cannot
  see each other's, because each asyncio task has its own context.
- An incoming `X-Correlation-ID` is accepted only if it parses as a UUID. The header is
  written by whoever calls the API, and its value ends up in every log line.
- The event name is a fixed string (`http.request`) and the variable parts are fields.
  That is what makes logs searchable: `event="http.request" and status>=500`.

**Seen in practice**
- Uvicorn's own lines (`INFO: Started server process`, its access log) are still plain
  text, not JSON. They need to go through the same pipeline before Loki (Stage 6).
- The `http.request` line logs the path. Paths will later contain email IDs — fine — but
  never query strings or bodies.

**Try it**
- `curl -i -H "X-Correlation-ID: 11111111-2222-4333-8444-555555555555" localhost:8000/healthz`
  and find the same ID in the server output. Repeat with `not-a-uuid`.
- Switch `LOG_FORMAT` between `json` and `console` in `.env` and restart.

**Not met yet**
- Metrics, Prometheus, Loki, Grafana, alerts (Stage 6).
- The email's own correlation ID, bound by the orchestrator rather than by an HTTP request (Stage 2).
