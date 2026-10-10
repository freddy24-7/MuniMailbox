import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.permissions import PermissionDeniedError
from app.db.models import Draft, Email, EmailStatus, OutboxMessage, Role, User
from app.orchestrator.ingestion import ingest_email
from app.privacy.crypto import Cipher
from app.privacy.restore import restore
from app.review.service import approve_draft
from app.services import Services
from tests.conftest import TEAM
from tests.helpers import BSN, incoming_email, logged_in

Db = async_sessionmaker[AsyncSession]
Users = dict[Role, User]


async def _team_a_draft(app: FastAPI) -> Draft:
    services: Services = app.state.services
    async with services.session_factory() as session:
        await ingest_email(session, services.bus, services.cipher, incoming_email(), team=TEAM)
        await services.bus.join()
        return (await session.scalars(select(Draft))).one()


async def test_a_caseworker_cannot_see_or_approve_another_teams_email(
    app: FastAPI, outsider: User, session_factory: Db, cipher: Cipher
) -> None:
    """Acceptance criterion: a caseworker cannot view another team's emails. The outsider
    has every permission a caseworker has; only the team differs."""
    draft = await _team_a_draft(app)

    async with logged_in(app, outsider) as client:
        queue = await client.get("/queue")
        assert queue.status_code == 200
        assert str(draft.email_id) not in queue.text

        # 404, not 403: the response does not confirm that the email exists.
        detail = await client.get(f"/emails/{draft.email_id}")
        assert detail.status_code == 404
        assert BSN not in detail.text
        assert (await client.post(f"/drafts/{draft.id}/approve")).status_code == 404

    async with session_factory() as session:
        email = await session.get_one(Email, draft.email_id)
        with pytest.raises(PermissionDeniedError):
            await approve_draft(session, cipher, user=outsider, draft_id=draft.id)
        with pytest.raises(PermissionDeniedError):
            await restore(session, cipher, user=outsider, email=email, texts=[draft.text_redacted])
        assert email.status is EmailStatus.DRAFT_READY
        assert await session.scalar(select(func.count()).select_from(OutboxMessage)) == 0


async def test_the_own_team_sees_it(app: FastAPI, users: Users) -> None:
    draft = await _team_a_draft(app)
    for role in (Role.CASEWORKER, Role.TEAM_LEAD):
        async with logged_in(app, users[role]) as client:
            assert str(draft.email_id) in (await client.get("/queue")).text
            assert (await client.get(f"/emails/{draft.email_id}")).status_code == 200


async def test_fetched_emails_belong_to_the_team_that_fetched_them(
    app: FastAPI, outsider: User, users: Users, session_factory: Db
) -> None:
    services: Services = app.state.services
    services.settings.feed_path.write_text(
        incoming_email().model_dump_json() + "\n", encoding="utf-8"
    )
    async with logged_in(app, outsider) as client:
        await client.post("/intake/next")
    await services.bus.join()

    async with session_factory() as session:
        email = (await session.scalars(select(Email))).one()
    assert email.team == outsider.team
    async with logged_in(app, users[Role.CASEWORKER]) as client:
        assert (await client.get(f"/emails/{email.id}")).status_code == 404
