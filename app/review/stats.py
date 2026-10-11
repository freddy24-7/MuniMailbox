"""Numbers for the team lead's dashboard (FR-22). Counts and durations only: nothing
here reads email text or personal data."""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.scope import visible_emails
from app.db.models import Draft, Email, EmailStatus, Review, ReviewAction, User

CLOSING_ACTIONS = (ReviewAction.APPROVE, ReviewAction.REJECT)


@dataclass(frozen=True)
class TeamStats:
    total: int
    by_topic: dict[str, int]
    by_status: dict[str, int]
    # From arrival in the system to the caseworker's decision; None until one is handled.
    average_handling_time: timedelta | None
    handled: int
    approved: int
    # Approved without any edit by a caseworker; None until a draft has been approved.
    approved_unchanged_share: float | None


async def team_stats(session: AsyncSession, user: User) -> TeamStats:
    """Over the emails this user may see, so a team lead gets their own team's numbers."""
    emails = (await session.scalars(select(Email).where(visible_emails(user)))).all()
    by_topic: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for email in emails:
        topic = email.topic.value if email.topic else "not classified"
        by_topic[topic] = by_topic.get(topic, 0) + 1
        by_status[email.status.value] = by_status.get(email.status.value, 0) + 1

    ingested_at = {email.id: email.ingested_at for email in emails}
    rows = (
        await session.execute(
            select(Draft.email_id, Review.action, Review.created_at)
            .join(Review, Review.draft_id == Draft.id)
            .where(Draft.email_id.in_(ingested_at))
        )
    ).all()
    edited = {email_id for email_id, action, _ in rows if action is ReviewAction.EDIT}
    approved = {email_id for email_id, action, _ in rows if action is ReviewAction.APPROVE}
    durations = [
        decided_at - ingested_at[email_id]
        for email_id, action, decided_at in rows
        if action in CLOSING_ACTIONS
    ]
    return TeamStats(
        total=len(emails),
        by_topic=dict(sorted(by_topic.items(), key=lambda item: -item[1])),
        by_status={
            status.value: by_status[status.value]
            for status in EmailStatus
            if status.value in by_status
        },
        average_handling_time=sum(durations, timedelta()) / len(durations) if durations else None,
        handled=len(durations),
        approved=len(approved),
        approved_unchanged_share=len(approved - edited) / len(approved) if approved else None,
    )
