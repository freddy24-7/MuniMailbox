import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import writer as audit
from app.db.models import Citizen, Email
from app.events.bus import Event, EventBus
from app.observability import metrics
from app.orchestrator.workflow import EMAIL_RECEIVED
from app.privacy.crypto import Cipher
from app.privacy.original import OriginalEmail


class IncomingEmail(BaseModel):
    """An email as the mailbox delivers it (FR-1). Unknown fields, such as the ground-truth
    labels in the synthetic feed, are ignored."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    sender: str
    subject: str
    body: str
    received_at: datetime
    attachments: list[str] = []


async def ingest_email(
    session: AsyncSession,
    bus: EventBus,
    cipher: Cipher,
    incoming: IncomingEmail,
    *,
    team: str | None = None,
) -> Email:
    citizen_id = await session.scalar(
        select(Citizen.id).where(Citizen.email == incoming.sender).limit(1)
    )
    email = Email(
        id=incoming.id,
        correlation_id=uuid.uuid4(),
        citizen_id=citizen_id,
        original_encrypted=OriginalEmail(incoming.sender, incoming.subject, incoming.body).encrypt(
            cipher
        ),
        attachments=incoming.attachments,
        received_at=incoming.received_at,
        team=team,
    )
    session.add(email)
    await audit.record(session, actor="system:intake", action=EMAIL_RECEIVED, email_id=email.id)
    await session.commit()
    metrics.EMAILS_RECEIVED.inc()
    # Published only after the commit: a subscriber must be able to find the email.
    await bus.publish(Event(EMAIL_RECEIVED, email.id, email.correlation_id))
    return email


def _read_feed(path: Path) -> list[IncomingEmail]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as feed:
        return [IncomingEmail.model_validate(json.loads(line)) for line in feed if line.strip()]


async def ingest_next_from_feed(
    session: AsyncSession,
    bus: EventBus,
    cipher: Cipher,
    feed_path: Path,
    *,
    team: str | None = None,
) -> Email | None:
    """Simulated mailbox: take the first feed email that has not been ingested yet."""
    feed = await asyncio.to_thread(_read_feed, feed_path)
    known = set(await session.scalars(select(Email.id)))
    for incoming in feed:
        if incoming.id not in known:
            return await ingest_email(session, bus, cipher, incoming, team=team)
    return None
