"""Edit, reject and reassign (FR-20): what a reviewer can do besides approving."""

import json

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.permissions import PermissionDeniedError
from app.db.models import (
    AuditEvent,
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
from app.orchestrator.ingestion import ingest_email
from app.privacy.crypto import Cipher
from app.privacy.redaction import Redactor
from app.review.service import edit_draft, reassign_email, reject_draft
from app.services import Services
from tests.conftest import TEAM
from tests.helpers import BSN, incoming_email, logged_in

Db = async_sessionmaker[AsyncSession]
Users = dict[Role, User]

NEW_EMAIL = "collega@example.org"
EDITED = f"Geachte heer/mevrouw,\n\nUw BSN {BSN} klopt. Mail anders naar {NEW_EMAIL}.\n\nGemeente"


async def _email_with_draft(app: FastAPI) -> Draft:
    services: Services = app.state.services
    async with services.session_factory() as session:
        await ingest_email(session, services.bus, services.cipher, incoming_email(), team=TEAM)
        await services.bus.join()
        return (await session.scalars(select(Draft))).one()


async def _state(session_factory: Db, draft: Draft) -> tuple[Email, list[Review], int, list[str]]:
    async with session_factory() as session:
        return (
            await session.get_one(Email, draft.email_id),
            list(await session.scalars(select(Review).order_by(Review.created_at))),
            await session.scalar(select(func.count()).select_from(OutboxMessage)) or 0,
            list(await session.scalars(select(AuditEvent.action).order_by(AuditEvent.id))),
        )


# --- Edit ---


async def test_an_edit_is_stored_redacted_and_is_what_gets_sent(
    app: FastAPI, users: Users, session_factory: Db, cipher: Cipher
) -> None:
    """The caseworker types in restored text. What they type is redacted again before it
    is stored, including personal data that was not in the email."""
    draft = await _email_with_draft(app)

    async with logged_in(app, users[Role.CASEWORKER]) as client:
        saved = await client.post(f"/drafts/{draft.id}/edit", data={"text": EDITED})
        assert saved.status_code == 303
        page = (await client.get(f"/emails/{draft.email_id}")).text
        assert f"Uw BSN {BSN} klopt." in page  # shown restored, in the edit box
        assert "Edited by a caseworker" in page
        assert (await client.post(f"/drafts/{draft.id}/approve")).status_code == 303

    email, (edit, approval), _, actions = await _state(session_factory, draft)
    assert (edit.action, approval.action) == (ReviewAction.EDIT, ReviewAction.APPROVE)
    stored = edit.edited_text_redacted or ""
    assert "Uw BSN [BSN_1] klopt." in stored  # the value it already knew: same placeholder
    assert BSN not in stored
    assert NEW_EMAIL not in stored  # the value it did not know: a new placeholder
    async with session_factory() as session:
        unchanged = await session.get_one(Draft, draft.id)
        outbox = (await session.scalars(select(OutboxMessage))).one()
        mappings = (await session.scalars(select(PiiMapping))).all()
    assert unchanged.text_redacted == draft.text_redacted  # the model's version is kept
    assert all(NEW_EMAIL.encode() not in mapping.encrypted_value for mapping in mappings)
    sent = json.loads(cipher.decrypt(outbox.payload_encrypted))
    assert sent["body"] == EDITED
    assert email.status is EmailStatus.APPROVED
    assert "review.edited" in actions


async def test_the_last_edit_wins(
    app: FastAPI, users: Users, session_factory: Db, cipher: Cipher
) -> None:
    draft = await _email_with_draft(app)
    async with logged_in(app, users[Role.CASEWORKER]) as client:
        for text in ("Eerste versie.", "Tweede versie."):
            await client.post(f"/drafts/{draft.id}/edit", data={"text": text})
        await client.post(f"/drafts/{draft.id}/approve")

    async with session_factory() as session:
        outbox = (await session.scalars(select(OutboxMessage))).one()
    assert json.loads(cipher.decrypt(outbox.payload_encrypted))["body"] == "Tweede versie."


async def test_an_empty_reply_cannot_be_saved(
    app: FastAPI, users: Users, session_factory: Db
) -> None:
    draft = await _email_with_draft(app)
    async with logged_in(app, users[Role.CASEWORKER]) as client:
        assert (
            await client.post(f"/drafts/{draft.id}/edit", data={"text": "  "})
        ).status_code == 409
    _, reviews, _, _ = await _state(session_factory, draft)
    assert reviews == []


# --- Reject ---


async def test_a_rejection_needs_a_reason_and_closes_the_email_without_sending(
    app: FastAPI, users: Users, session_factory: Db
) -> None:
    draft = await _email_with_draft(app)
    reason = f"Verkeerde persoon: BSN {BSN} hoort hier niet bij."

    async with logged_in(app, users[Role.CASEWORKER]) as client:
        blank = await client.post(f"/drafts/{draft.id}/reject", data={"reason": " "})
        assert blank.status_code == 409
        rejected = await client.post(f"/drafts/{draft.id}/reject", data={"reason": reason})
        assert rejected.status_code == 303
        assert reason in (await client.get(f"/emails/{draft.email_id}")).text
        # Closed means closed: no approving or editing afterwards.
        assert (await client.post(f"/drafts/{draft.id}/approve")).status_code == 409
        again = await client.post(f"/drafts/{draft.id}/edit", data={"text": "Toch."})
        assert again.status_code == 409

    email, (review,), outbox_count, actions = await _state(session_factory, draft)
    assert email.status is EmailStatus.REJECTED
    assert review.action is ReviewAction.REJECT
    assert review.reason == "Verkeerde persoon: BSN [BSN_1] hoort hier niet bij."
    assert outbox_count == 0
    assert "review.rejected" in actions
    assert "outbox.written" not in actions


# --- Who may do what ---


@pytest.mark.parametrize("role", [Role.TEAM_LEAD, Role.AUDITOR])
async def test_only_a_caseworker_can_edit_or_reject(
    app: FastAPI,
    users: Users,
    session_factory: Db,
    cipher: Cipher,
    redactor: Redactor,
    role: Role,
) -> None:
    """Checked at the endpoint and again in the service behind it."""
    draft = await _email_with_draft(app)

    async with logged_in(app, users[role]) as client:
        edit = await client.post(f"/drafts/{draft.id}/edit", data={"text": "Nieuw."})
        reject = await client.post(f"/drafts/{draft.id}/reject", data={"reason": "Nee."})
        assert (edit.status_code, reject.status_code) == (403, 403)

    async with session_factory() as session:
        with pytest.raises(PermissionDeniedError):
            await edit_draft(
                session, cipher, redactor, user=users[role], draft_id=draft.id, text="Nieuw."
            )
        with pytest.raises(PermissionDeniedError):
            await reject_draft(
                session, cipher, redactor, user=users[role], draft_id=draft.id, reason="Nee."
            )

    email, reviews, _, _ = await _state(session_factory, draft)
    assert (email.status, reviews) == (EmailStatus.DRAFT_READY, [])


async def test_a_caseworker_of_another_team_can_do_nothing_with_the_email(
    app: FastAPI, outsider: User, session_factory: Db, cipher: Cipher, redactor: Redactor
) -> None:
    """Every permission, wrong team: the scope check is the only thing that stops them."""
    draft = await _email_with_draft(app)

    async with logged_in(app, outsider) as client:
        responses = [
            await client.post(f"/drafts/{draft.id}/edit", data={"text": "Nieuw."}),
            await client.post(f"/drafts/{draft.id}/reject", data={"reason": "Nee."}),
            await client.post(f"/emails/{draft.email_id}/reassign", data={"team": "Team B"}),
        ]
    assert [response.status_code for response in responses] == [404, 404, 404]

    async with session_factory() as session:
        with pytest.raises(PermissionDeniedError):
            await reassign_email(session, user=outsider, email_id=draft.email_id, team="Team B")

    email, reviews, _, _ = await _state(session_factory, draft)
    assert (email.status, email.team, reviews) == (EmailStatus.DRAFT_READY, TEAM, [])


# --- Reassign ---


async def _get(client: httpx.AsyncClient, draft: Draft) -> int:
    return (await client.get(f"/emails/{draft.email_id}")).status_code


@pytest.mark.parametrize("role", [Role.CASEWORKER, Role.TEAM_LEAD])
async def test_reassigning_to_another_team_moves_who_can_see_the_email(
    app: FastAPI, users: Users, outsider: User, session_factory: Db, role: Role
) -> None:
    draft = await _email_with_draft(app)

    async with logged_in(app, users[role]) as client:
        moved = await client.post(f"/emails/{draft.email_id}/reassign", data={"team": "Team B"})
        assert (moved.status_code, moved.headers["location"]) == (303, "/queue")
        assert await _get(client, draft) == 404  # no longer theirs to see
    async with logged_in(app, outsider) as client:
        assert await _get(client, draft) == 200

    email, _, _, actions = await _state(session_factory, draft)
    assert (email.team, email.assigned_to, email.status) == (
        "Team B",
        None,
        EmailStatus.DRAFT_READY,
    )
    assert "email.reassigned" in actions


async def test_assigning_to_a_caseworker_also_moves_the_email_to_their_team(
    app: FastAPI, users: Users, outsider: User, session_factory: Db
) -> None:
    draft = await _email_with_draft(app)
    async with logged_in(app, users[Role.CASEWORKER]) as client:
        await client.post(
            f"/emails/{draft.email_id}/reassign", data={"team": TEAM, "assignee": str(outsider.id)}
        )
    email, _, _, _ = await _state(session_factory, draft)
    assert (email.team, email.assigned_to) == ("Team B", outsider.id)


async def test_reassignment_is_refused_when_it_makes_no_sense(
    app: FastAPI, users: Users, session_factory: Db
) -> None:
    draft = await _email_with_draft(app)
    url = f"/emails/{draft.email_id}/reassign"

    async with logged_in(app, users[Role.AUDITOR]) as client:
        assert (await client.post(url, data={"team": "Team B"})).status_code == 403
    async with logged_in(app, users[Role.CASEWORKER]) as client:
        # A team nobody is in, a user who is not a caseworker, an id that is not one.
        unknown = await client.post(url, data={"team": "Team Z"})
        lead = await client.post(url, data={"assignee": str(users[Role.TEAM_LEAD].id)})
        garbage = await client.post(url, data={"assignee": "not-a-uuid"})
        assert [unknown.status_code, lead.status_code, garbage.status_code] == [409, 409, 422]
        # An email that is finished stays where it is.
        await client.post(f"/drafts/{draft.id}/approve")
        assert (await client.post(url, data={"team": TEAM})).status_code == 409

    email, _, _, actions = await _state(session_factory, draft)
    assert email.team == TEAM
    assert "email.reassigned" not in actions
