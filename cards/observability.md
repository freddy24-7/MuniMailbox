# Observability: metrics, logs, dashboards, alerts

**Status:** Stage 6 notes added, questions open · **Stages:** 1 (logs, correlation ID), 6 · **Requirements:** NFR-1, NFR-2, NFR-3 · **Code:** `app/observability/`, `deploy/`

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

### Stage 6 — 2026-10-11 (metrics, Loki, Grafana, alerts)

**What was built**
- [app/observability/metrics.py](../app/observability/metrics.py): the metrics, and
  `GET /metrics` in [app/api/health.py](../app/api/health.py) for Prometheus to scrape.
- A JSON copy of every log event in a file (`LOG_FILE`), for the log shipper.
- In `deploy/`, as a Compose profile that is off by default: Prometheus with six alert
  rules, Loki, Grafana Alloy (ships the log file to Loki) and Grafana with three
  dashboards: system health, agent performance, privacy events.

**The three kinds of record, and which question each answers**

| | Answers | Example here |
| --- | --- | --- |
| Metric | How much, how often, how fast? | Median duration of the drafting agent |
| Log | What happened to this one email? | Its nine lines, by correlation ID |
| Audit event | Who did what, and can that be proven? | "Caseworker X approved draft Y" |

**Decisions and why**
- **No identifiers in metrics.** A label has a small, fixed set of values (an agent name,
  `local` or `cloud`). An email ID as a label would create one time series per email. A
  test checks that the metrics page contains no email ID, correlation ID or personal value.
- **Histograms, not averages.** Durations are counted into buckets, so Prometheus can
  compute a median or a 95th percentile afterwards. An average hides the slow tail.
- **Counters are increased after the commit.** "Emails processed" only goes up once the
  result is really saved.
- **Queue length is read from the database at scrape time,** not counted up and down in
  memory. It is a fact about the data, and stays right after a restart.
- **The privacy alert does not trust the component it guards.** "Cloud calls without a
  redaction record" compares the audit trail with the redaction records. If the router's
  check were ever bypassed, this number would still show it. A test makes it show 1.
- **The correlation ID is a field in the log line, not a Loki label,** for the same
  reason as with metrics. It is found with `| json | correlation_id="..."`.
- **`/metrics` has no login,** because Prometheus cannot log in. It can require a token
  (`METRICS_TOKEN`), and the reverse proxy does not serve it to the internet.

**Seen in practice**
- With the stack running and two emails processed: Prometheus reported the app as up and
  each agent as run twice; Grafana had loaded both data sources and the three dashboards;
  Loki returned one email's journey in order (privacy 111 ms, intake 5 ms, ...).
- Before the app was started, the `AppDown` alert went to "pending" by itself. The first
  alert that ever fired in this project was a correct one.
- The four monitoring containers used about 170 MB together.
- Checked through the services' APIs; the dashboards were not looked at in a browser.

**Try it**
- `docker compose --profile monitoring up -d`, start the app, fetch an email, then open
  Grafana on http://localhost:3000 (admin / admin) → "Agent performance", and paste the
  email's correlation ID in the box at the top.
- `curl -s localhost:8000/metrics | grep munimailbox_emails`

**Still open**
- Alerts are only shown; nothing sends an email or a message when one fires.
- No metric for the event bus itself (how many events are waiting or unacknowledged).
