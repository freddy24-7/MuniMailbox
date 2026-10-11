"""Prometheus metrics (NFR-1).

Metrics answer "how much, how often, how fast". They carry no email content and no
identifiers: a label has a small, fixed set of values (an agent name, a route), never an
email ID. One email's journey is a question for the logs, by correlation ID.
"""

from datetime import UTC, datetime

from prometheus_client import Counter, Gauge, Histogram
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditEvent, Email, EmailStatus, RedactionRecord

_SECONDS = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 30, 60, 120, 300)

EMAILS_RECEIVED = Counter("munimailbox_emails_received_total", "Emails ingested.")
EMAILS_PROCESSED = Counter(
    "munimailbox_emails_processed_total",
    "Emails that left the pipeline, by how it ended.",
    ["outcome"],  # draft_ready | needs_attention
)
TIME_TO_DRAFT = Histogram(
    "munimailbox_time_to_draft_seconds",
    "From ingestion to a draft ready for review.",
    buckets=_SECONDS,
)
AGENT_DURATION = Histogram(
    "munimailbox_agent_duration_seconds",
    "Duration of one successful agent run.",
    ["agent"],
    buckets=_SECONDS,
)
AGENT_FAILURES = Counter(
    "munimailbox_agent_failures_total",
    "Agent runs that failed for good, after the retries.",
    ["agent"],
)
AGENT_RETRIES = Counter("munimailbox_agent_retries_total", "Agent runs retried.", ["agent"])
INFERENCE_CALLS = Counter(
    "munimailbox_inference_calls_total",
    "Completed model calls, by where they ran.",
    ["route", "model"],  # route: local | cloud
)
INFERENCE_TOKENS = Counter(
    "munimailbox_inference_tokens_total",
    "Tokens used by model calls.",
    ["route", "kind"],  # kind: prompt | completion
)
INFERENCE_REFUSALS = Counter(
    "munimailbox_inference_refusals_total",
    "Model calls the router refused because the input was not redacted (FR-14).",
    ["reason"],  # no_redaction_record | unredacted_value
)
LOCAL_FALLBACKS = Counter(
    "munimailbox_inference_local_failures_total",
    "Local model failures that were retried on the cloud model.",
)
REDACTIONS = Counter(
    "munimailbox_redactions_total", "Personal values replaced, by type.", ["entity_type"]
)
REDACTIONS_PER_EMAIL = Histogram(
    "munimailbox_redactions_per_email",
    "Personal values replaced in one email.",
    buckets=(0, 1, 2, 3, 5, 8, 13, 21),
)
QUEUE_LENGTH = Gauge("munimailbox_emails_in_status", "Emails currently in each status.", ["status"])
CLOUD_WITHOUT_REDACTION = Gauge(
    "munimailbox_cloud_calls_without_redaction_record",
    "Audited model calls that left the local environment for an email with no redaction "
    "record. Must be 0; anything else is a privacy incident.",
)


def seconds_since(moment: datetime) -> float:
    # SQLite hands timestamps back without a zone; they were stored as UTC.
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return max((datetime.now(UTC) - moment).total_seconds(), 0.0)


async def refresh_gauges(session: AsyncSession) -> None:
    """Values that are a fact about the database, read at scrape time.

    The last one is deliberately independent of the router: it compares the audit trail
    with the redaction records, so it would also notice a call that got past the check.
    """
    counts = dict(
        (await session.execute(select(Email.status, func.count()).group_by(Email.status))).all()
    )
    for status in EmailStatus:
        QUEUE_LENGTH.labels(status=status.value).set(counts.get(status, 0))

    unredacted = await session.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(
            AuditEvent.left_local.is_(True),
            AuditEvent.email_id.is_not(None),
            AuditEvent.email_id.not_in(select(RedactionRecord.email_id)),
        )
    )
    CLOUD_WITHOUT_REDACTION.set(unredacted or 0)
