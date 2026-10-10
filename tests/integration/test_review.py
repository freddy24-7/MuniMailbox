import json

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.permissions import PermissionDeniedError
from app.db.models import AuditEvent, Draft, Email, EmailStatus, OutboxMessage, Review, Role, User
from app.orchestrator.ingestion import IncomingEmail, ingest_email
from app.privacy.crypto import Cipher
from app.privacy.restore import restore
from app.review.service import approve_draft
from app.services import Services
from tests.conftest import TEAM
from tests.helpers import BSN, SENDER, incoming_email, logged_in

Db = async_sessionmaker[AsyncSession]
Users = dict[Role, User]


async def _email_with_draft(app: FastAPI, incoming: IncomingEmail | None = None) -> Draft:
    services: Services = app.state.services
    async with services.session_factory() as session:
        await ingest_email(
            session, services.bus, services.cipher, incoming or incoming_email(), team=TEAM
        )
        await services.bus.join()
        return (await session.scalars(select(Draft))).one()


async def _outbox_count(session_factory: Db) -> int:
    async with session_factory() as session:
        return await session.scalar(select(func.count()).select_from(OutboxMessage)) or 0


async def test_without_login_every_page_leads_to_the_login_page(app: FastAPI) -> None:
    draft = await _email_with_draft(app)
    async with logged_in(app, None) as client:
        for response in (
            await client.get("/queue"),
            await client.get(f"/emails/{draft.email_id}"),
            await client.post(f"/drafts/{draft.id}/approve"),
            await client.get("/audit"),
        ):
            assert (response.status_code, response.headers["location"]) == (303, "/login")


async def test_a_forged_cookie_is_not_a_login(app: FastAPI, users: Users) -> None:
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        cookies={"session": str(users[Role.CASEWORKER].id)},
    )
    async with client:
        assert (await client.get("/queue")).status_code == 303


async def test_caseworker_sees_restored_data_and_approves(
    app: FastAPI, users: Users, session_factory: Db, cipher: Cipher
) -> None:
    draft = await _email_with_draft(app)
    assert BSN not in draft.text_redacted

    async with logged_in(app, users[Role.CASEWORKER]) as client:
        queue = await client.get("/queue")
        assert BSN not in queue.text  # the queue shows redacted subjects only

        detail = await client.get(f"/emails/{draft.email_id}")
        assert detail.status_code == 200
        assert f"Uw BSN {BSN} is bekend." in detail.text  # the draft, restored for display

        approved = await client.post(f"/drafts/{draft.id}/approve")
        assert approved.status_code == 303

    async with session_factory() as session:
        email = await session.get_one(Email, draft.email_id)
        review = (await session.scalars(select(Review))).one()
        outbox = (await session.scalars(select(OutboxMessage))).one()
        stored_draft = await session.get_one(Draft, draft.id)
        actions = list(await session.scalars(select(AuditEvent.action).order_by(AuditEvent.id)))
        approval = await session.scalar(
            select(AuditEvent.actor).where(AuditEvent.action == "review.approved")
        )

    assert email.status is EmailStatus.APPROVED
    assert (review.user_id, review.action.value) == (users[Role.CASEWORKER].id, "approve")
    assert outbox.review_id == review.id
    sent = json.loads(cipher.decrypt(outbox.payload_encrypted))
    assert sent["to"] == SENDER
    assert BSN in sent["body"]
    assert BSN not in stored_draft.text_redacted  # the stored draft keeps its placeholders
    assert approval == f"user:{users[Role.CASEWORKER].id}"
    assert actions[-3:] == ["email.viewed_restored", "review.approved", "outbox.written"]


@pytest.mark.parametrize("role", [Role.TEAM_LEAD, Role.AUDITOR])
async def test_other_roles_cannot_approve(
    app: FastAPI, users: Users, session_factory: Db, cipher: Cipher, role: Role
) -> None:
    """CLAUDE.md rule 3, checked at the endpoint and again in the service behind it."""
    draft = await _email_with_draft(app)

    async with logged_in(app, users[role]) as client:
        assert (await client.post(f"/drafts/{draft.id}/approve")).status_code == 403

    async with session_factory() as session:
        with pytest.raises(PermissionDeniedError):
            await approve_draft(session, cipher, user=users[role], draft_id=draft.id)

    assert await _outbox_count(session_factory) == 0


async def test_auditor_sees_the_process_but_never_the_person(
    app: FastAPI, users: Users, session_factory: Db, cipher: Cipher
) -> None:
    """Acceptance criterion: an auditor account cannot view restored personal data."""
    draft = await _email_with_draft(app)

    async with logged_in(app, users[Role.AUDITOR]) as client:
        assert (await client.get(f"/emails/{draft.email_id}")).status_code == 403
        assert (await client.get("/queue")).status_code == 403

        audit_page = await client.get("/audit")
        assert audit_page.status_code == 200
        assert "privacy.completed" in audit_page.text
        assert "Hash chain intact" in audit_page.text
        assert BSN not in audit_page.text
        assert SENDER not in audit_page.text

    async with session_factory() as session:
        with pytest.raises(PermissionDeniedError):
            await restore(
                session,
                cipher,
                user=users[Role.AUDITOR],
                email=await session.get_one(Email, draft.email_id),
                texts=[draft.text_redacted],
            )


async def test_a_draft_can_be_approved_only_once(
    app: FastAPI, users: Users, session_factory: Db
) -> None:
    draft = await _email_with_draft(app)
    async with logged_in(app, users[Role.CASEWORKER]) as client:
        assert (await client.post(f"/drafts/{draft.id}/approve")).status_code == 303
        assert (await client.post(f"/drafts/{draft.id}/approve")).status_code == 409
    assert await _outbox_count(session_factory) == 1


async def test_email_text_is_escaped_in_the_page(app: FastAPI, users: Users) -> None:
    draft = await _email_with_draft(app, incoming_email("<script>alert('x')</script>"))
    async with logged_in(app, users[Role.CASEWORKER]) as client:
        page = (await client.get(f"/emails/{draft.email_id}")).text
    assert "<script>alert(" not in page
    assert "&lt;script&gt;alert(" in page


async def test_feed_button_ingests_the_next_email(
    app: FastAPI, users: Users, session_factory: Db
) -> None:
    services: Services = app.state.services
    lines = [incoming_email(f"Nummer {number}.").model_dump_json() for number in range(2)]
    services.settings.feed_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    async with logged_in(app, users[Role.CASEWORKER]) as client:
        for expected in (1, 2, 2):  # the third click finds nothing new
            assert (await client.post("/intake/next")).status_code == 303
            await services.bus.join()
            async with session_factory() as session:
                count = await session.scalar(select(func.count()).select_from(Email))
            assert count == expected
