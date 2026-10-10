from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import writer as audit
from app.db.models import Email, PiiMapping


async def purge_expired(
    session: AsyncSession, *, retention_days: int, now: datetime | None = None
) -> int:
    """Delete the original and the PII mapping of every email held longer than the
    retention period (GR-2). Returns how many emails were purged; the caller commits.

    What remains is the redacted text. Without the mapping nobody, including us, can
    turn its placeholders back into a person.
    """
    cutoff = (now or datetime.now(UTC)) - timedelta(days=retention_days)
    expired = (
        await session.scalars(
            select(Email).where(Email.ingested_at < cutoff, Email.original_encrypted.is_not(None))
        )
    ).all()
    for email in expired:
        await session.execute(delete(PiiMapping).where(PiiMapping.email_id == email.id))
        email.original_encrypted = None
        await audit.record(
            session, actor="system:retention", action="pii.purged", email_id=email.id
        )
    return len(expired)
