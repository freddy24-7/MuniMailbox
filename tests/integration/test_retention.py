from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.writer import verify_chain
from app.db.models import AuditEvent, Draft, Email, PiiMapping, Role, User
from app.orchestrator.ingestion import ingest_email
from app.privacy.retention import purge_expired
from app.services import Services
from tests.conftest import TEAM
from tests.helpers import BSN, SENDER, incoming_email, logged_in

Db = async_sessionmaker[AsyncSession]
RETENTION_DAYS = 30


async def _draft(app: FastAPI) -> Draft:
    services: Services = app.state.services
    async with services.session_factory() as session:
        await ingest_email(session, services.bus, services.cipher, incoming_email(), team=TEAM)
        await services.bus.join()
        return (await session.scalars(select(Draft))).one()


async def _purge(session_factory: Db, *, days_from_now: int) -> int:
    async with session_factory() as session:
        purged = await purge_expired(
            session,
            retention_days=RETENTION_DAYS,
            now=datetime.now(UTC) + timedelta(days=days_from_now),
        )
        await session.commit()
    return purged


async def test_nothing_is_deleted_inside_the_retention_period(
    app: FastAPI, session_factory: Db
) -> None:
    draft = await _draft(app)
    assert await _purge(session_factory, days_from_now=RETENTION_DAYS - 1) == 0

    async with session_factory() as session:
        email = await session.get_one(Email, draft.email_id)
        assert email.original_encrypted is not None
        assert await session.scalar(select(func.count()).select_from(PiiMapping))


async def test_after_the_period_the_person_is_gone_and_the_process_remains(
    app: FastAPI, users: dict[Role, User], session_factory: Db
) -> None:
    draft = await _draft(app)
    assert await _purge(session_factory, days_from_now=RETENTION_DAYS + 1) == 1
    assert await _purge(session_factory, days_from_now=RETENTION_DAYS + 1) == 0  # only once

    async with session_factory() as session:
        email = await session.get_one(Email, draft.email_id)
        assert email.original_encrypted is None
        assert await session.scalar(select(func.count()).select_from(PiiMapping)) == 0
        # What is kept: the redacted text, the draft and the audit trail.
        assert "[BSN_1]" in (email.body_redacted or "")
        assert await session.get(Draft, draft.id) is not None
        last = await session.scalar(select(AuditEvent.action).order_by(AuditEvent.id.desc()))
        assert last == "pii.purged"
        assert (await verify_chain(session)).ok

    async with logged_in(app, users[Role.CASEWORKER]) as client:
        page = await client.get(f"/emails/{draft.email_id}")
        assert page.status_code == 200
        assert "deleted after the retention period" in page.text
        assert BSN not in page.text
        assert SENDER not in page.text
        # A reply can no longer be addressed or completed, so it cannot be approved.
        assert (await client.post(f"/drafts/{draft.id}/approve")).status_code == 409
