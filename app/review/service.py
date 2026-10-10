import json
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import writer as audit
from app.auth.permissions import Permission, require_permission
from app.auth.scope import require_email_access
from app.db.models import (
    Draft,
    Email,
    EmailStatus,
    OutboxMessage,
    Review,
    ReviewAction,
    User,
)
from app.db.status import transition
from app.privacy.crypto import Cipher
from app.privacy.restore import read_original, restore


class NotApprovableError(Exception):
    pass


async def approve_draft(
    session: AsyncSession, cipher: Cipher, *, user: User, draft_id: uuid.UUID
) -> OutboxMessage:
    """The only code path into the outbox (CLAUDE.md rule 3, FR-21).

    Review, outbox message, status change and audit events are one transaction; the
    caller commits. This is the only place an OutboxMessage is created.

    Raises PermissionDeniedError if the user's role may not approve or the email is
    outside their team, NotApprovableError if the draft cannot be approved (any more).
    """
    require_permission(user.role, Permission.DRAFT_APPROVE)

    draft = await session.get(Draft, draft_id)
    if draft is None:
        raise NotApprovableError("draft not found")
    # Row lock: two clicks on "approve" cannot both pass the status check.
    email = (
        await session.scalars(select(Email).where(Email.id == draft.email_id).with_for_update())
    ).one()
    require_email_access(user, email)
    if email.status is not EmailStatus.DRAFT_READY:
        raise NotApprovableError(f"email is {email.status.value}, not draft_ready")
    original = read_original(cipher, user=user, email=email)
    if original is None:
        raise NotApprovableError("personal data was deleted after the retention period")

    review = Review(draft_id=draft.id, user_id=user.id, action=ReviewAction.APPROVE)
    session.add(review)
    await session.flush()

    (body,) = await restore(session, cipher, user=user, email=email, texts=[draft.text_redacted])
    outbox_message = OutboxMessage(
        email_id=email.id,
        review_id=review.id,
        payload_encrypted=cipher.encrypt(
            json.dumps(
                {"to": original.sender, "subject": f"Re: {original.subject}", "body": body},
                ensure_ascii=False,
            )
        ),
    )
    session.add(outbox_message)
    transition(email, EmailStatus.APPROVED)

    await audit.record(
        session, actor=f"user:{user.id}", action="review.approved", email_id=email.id
    )
    await audit.record(session, actor="system:outbox", action="outbox.written", email_id=email.id)
    return outbox_message
