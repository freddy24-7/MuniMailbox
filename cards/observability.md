# Observability: metrics, logs, dashboards, alerts

**Status:** Overview · **Stages:** 1 (logs, correlation ID), 6 · **Requirements:** NFR-1, NFR-2, NFR-3 · **Code:** `app/observability/`, `deploy/`

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
_Empty — filled in during Stages 1 and 6._
