import asyncio
import json
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import writer as audit
from app.auth.permissions import Permission, require_permission
from app.auth.scope import require_email_access
from app.db.models import (
    Citizen,
    Draft,
    Email,
    EmailStatus,
    OutboxMessage,
    PiiMapping,
    Review,
    ReviewAction,
    Role,
    User,
)
from app.db.status import transition
from app.privacy.crypto import Cipher
from app.privacy.hints import known_values_for
from app.privacy.redaction import MappingEntry, Redactor
from app.privacy.restore import read_original, restore

# Statuses in which an email can still be handed to someone else.
REASSIGNABLE = frozenset({EmailStatus.DRAFT_READY, EmailStatus.NEEDS_ATTENTION})


class NotApprovableError(Exception):
    """The requested review action is not possible for this draft or email (any more)."""


async def _draft_under_review(
    session: AsyncSession, user: User, draft_id: uuid.UUID
) -> tuple[Draft, Email]:
    """The draft and its email, locked, checked for scope and for being open to review."""
    draft = await session.get(Draft, draft_id)
    if draft is None:
        raise NotApprovableError("draft not found")
    # Row lock: two clicks on a button cannot both pass the status check.
    email = (
        await session.scalars(select(Email).where(Email.id == draft.email_id).with_for_update())
    ).one()
    require_email_access(user, email)
    if email.status is not EmailStatus.DRAFT_READY:
        raise NotApprovableError(f"email is {email.status.value}, not draft_ready")
    return draft, email


async def current_text(session: AsyncSession, draft: Draft) -> str:
    """The reply as it stands: the caseworker's last edit, or else what the model wrote.

    The model's draft is never overwritten, so it stays possible to see what was changed
    and how often a draft was approved as it came (FR-22).
    """
    edited = await session.scalar(
        select(Review.edited_text_redacted)
        .where(Review.draft_id == draft.id, Review.action == ReviewAction.EDIT)
        .order_by(Review.created_at.desc(), Review.id.desc())
        .limit(1)
    )
    return edited if edited is not None else draft.text_redacted


async def _redact_again(
    session: AsyncSession, cipher: Cipher, redactor: Redactor, email: Email, text: str
) -> str:
    """Text a caseworker typed, with personal data replaced before it is stored.

    They work in the restored text, so it contains the citizen's details again and maybe
    new ones. The email's own mapping is continued: a value that already has a placeholder
    gets it back, a new value gets the next number.
    """
    rows = (await session.scalars(select(PiiMapping).where(PiiMapping.email_id == email.id))).all()
    existing = [
        MappingEntry(row.placeholder, row.entity_type, cipher.decrypt(row.encrypted_value))
        for row in rows
    ]
    citizen = await session.get(Citizen, email.citizen_id) if email.citizen_id else None
    result = await asyncio.to_thread(
        redactor.redact, [text], known_values_for(citizen) if citizen else [], existing
    )
    known_placeholders = {entry.placeholder for entry in existing}
    session.add_all(
        PiiMapping(
            email_id=email.id,
            placeholder=entry.placeholder,
            encrypted_value=cipher.encrypt(entry.value),
            entity_type=entry.entity_type,
        )
        for entry in result.mapping
        if entry.placeholder not in known_placeholders
    )
    return result.texts[0]


async def edit_draft(
    session: AsyncSession,
    cipher: Cipher,
    redactor: Redactor,
    *,
    user: User,
    draft_id: uuid.UUID,
    text: str,
) -> Review:
    """Save the caseworker's version of the reply (FR-20). Sends nothing."""
    require_permission(user.role, Permission.DRAFT_EDIT)
    draft, email = await _draft_under_review(session, user, draft_id)
    if email.original_encrypted is None:
        raise NotApprovableError("personal data was deleted after the retention period")
    if not text.strip():
        raise NotApprovableError("a reply cannot be empty")

    review = Review(
        draft_id=draft.id,
        user_id=user.id,
        action=ReviewAction.EDIT,
        edited_text_redacted=await _redact_again(session, cipher, redactor, email, text.strip()),
    )
    session.add(review)
    await audit.record(session, actor=f"user:{user.id}", action="review.edited", email_id=email.id)
    return review


async def reject_draft(
    session: AsyncSession,
    cipher: Cipher,
    redactor: Redactor,
    *,
    user: User,
    draft_id: uuid.UUID,
    reason: str,
) -> Review:
    """Turn the draft down (FR-20). The email is closed as rejected and nothing is sent."""
    require_permission(user.role, Permission.DRAFT_REJECT)
    draft, email = await _draft_under_review(session, user, draft_id)
    if not reason.strip():
        raise NotApprovableError("a rejection needs a reason")

    review = Review(
        draft_id=draft.id,
        user_id=user.id,
        action=ReviewAction.REJECT,
        # Free text about a citizen's email: it may name them.
        reason=await _redact_again(session, cipher, redactor, email, reason.strip()),
    )
    session.add(review)
    transition(email, EmailStatus.REJECTED)
    await audit.record(
        session, actor=f"user:{user.id}", action="review.rejected", email_id=email.id
    )
    return review


async def reassign_email(
    session: AsyncSession,
    *,
    user: User,
    email_id: uuid.UUID,
    team: str | None = None,
    assignee_id: uuid.UUID | None = None,
) -> Email:
    """Hand the email to another team, or to one caseworker (FR-20).

    Naming a caseworker also moves the email to their team. Afterwards the email is
    visible to the new team and no longer to the old one.
    """
    require_permission(user.role, Permission.EMAIL_REASSIGN)
    email = (
        await session.scalars(select(Email).where(Email.id == email_id).with_for_update())
    ).one_or_none()
    if email is None:
        raise NotApprovableError("email not found")
    require_email_access(user, email)
    if email.status not in REASSIGNABLE:
        raise NotApprovableError(f"email is {email.status.value} and cannot be reassigned")

    assignee = await session.get(User, assignee_id) if assignee_id else None
    if assignee_id and (assignee is None or assignee.role is not Role.CASEWORKER):
        raise NotApprovableError("an email can only be assigned to a caseworker")
    if assignee is not None:
        team = assignee.team
    if not team or team not in set(await session.scalars(select(User.team))):
        raise NotApprovableError("unknown team")

    email.team = team
    email.assigned_to = assignee.id if assignee else None
    await audit.record(
        session, actor=f"user:{user.id}", action="email.reassigned", email_id=email.id
    )
    return email


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
    draft, email = await _draft_under_review(session, user, draft_id)
    original = read_original(cipher, user=user, email=email)
    if original is None:
        raise NotApprovableError("personal data was deleted after the retention period")

    review = Review(draft_id=draft.id, user_id=user.id, action=ReviewAction.APPROVE)
    session.add(review)
    await session.flush()

    # What is sent is the reply as the caseworker left it, not necessarily as the model wrote it.
    (body,) = await restore(
        session, cipher, user=user, email=email, texts=[await current_text(session, draft)]
    )
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
