import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.drafting import DraftingAgent
from app.audit.writer import verify_chain
from app.db.models import AuditEvent, Draft, Email, EmailStatus, PiiMapping
from app.events.bus import InMemoryEventBus
from app.inference.router import InferenceRouter
from app.orchestrator.ingestion import IncomingEmail, ingest_email
from app.orchestrator.workflow import Orchestrator
from app.privacy.crypto import Cipher
from app.services import Services
from tests.helpers import BSN, IBAN, POSTCODE, SENDER, FakeLLM, incoming_email

Db = async_sessionmaker[AsyncSession]
PERSONAL_VALUES = (BSN, SENDER, POSTCODE, IBAN)


async def _ingest(app: FastAPI, incoming: IncomingEmail) -> Email:
    services: Services = app.state.services
    async with services.session_factory() as session:
        email = await ingest_email(session, services.bus, services.cipher, incoming)
    await services.bus.join()
    return email


async def _actions(session: AsyncSession, email: Email) -> list[str]:
    return list(
        await session.scalars(
            select(AuditEvent.action).where(AuditEvent.email_id == email.id).order_by(AuditEvent.id)
        )
    )


async def test_email_travels_from_intake_to_draft_ready(
    app: FastAPI, session_factory: Db, fake_llm: FakeLLM
) -> None:
    email = await _ingest(app, incoming_email())

    async with session_factory() as session:
        stored = await session.get_one(Email, email.id)
        draft = (await session.scalars(select(Draft))).one()
        mappings = (await session.scalars(select(PiiMapping))).all()
        actions = await _actions(session, email)
        chain = await verify_chain(session)

    assert stored.status is EmailStatus.DRAFT_READY
    assert draft.text_redacted == fake_llm.reply
    assert actions == [
        "email.received",
        "privacy.completed",
        "inference.completed",
        "drafting.completed",
    ]
    assert chain.ok

    # Acceptance criterion: the stored draft and all LLM inputs contain placeholders only.
    ((system_prompt, user_prompt),) = fake_llm.calls
    assert "[BSN_1]" in user_prompt
    for value in PERSONAL_VALUES:
        assert value not in system_prompt
        assert value not in user_prompt
        assert value not in (stored.subject_redacted or "")
        assert value not in (stored.body_redacted or "")
        # Nothing in the database holds the value in clear text either.
        assert value.encode() not in stored.original_encrypted
        assert all(value.encode() not in mapping.encrypted_value for mapping in mappings)


async def test_a_failing_model_sends_the_email_to_a_human(
    app: FastAPI, session_factory: Db, fake_llm: FakeLLM
) -> None:
    fake_llm.error = RuntimeError("model is down")
    email = await _ingest(app, incoming_email())

    async with session_factory() as session:
        stored = await session.get_one(Email, email.id)
        assert stored.status is EmailStatus.NEEDS_ATTENTION
        assert (await session.scalars(select(Draft))).all() == []
        assert (await _actions(session, email))[-1] == "drafting.failed"


async def test_a_workflow_that_skips_the_privacy_agent_never_reaches_the_model(
    session_factory: Db, cipher: Cipher, fake_llm: FakeLLM
) -> None:
    """The bypass test for CLAUDE.md rule 1: build the pipeline wrongly on purpose."""
    bus = InMemoryEventBus(workers=1)
    Orchestrator(
        bus=bus,
        session_factory=session_factory,
        agents=[DraftingAgent(router=InferenceRouter(local=fake_llm, cipher=cipher))],
    ).register()
    await bus.start()
    async with session_factory() as session:
        email = await ingest_email(session, bus, cipher, incoming_email())
    await bus.join()
    await bus.stop()

    assert fake_llm.calls == []
    async with session_factory() as session:
        stored = await session.get_one(Email, email.id)
        assert stored.status is EmailStatus.NEEDS_ATTENTION
        assert (await _actions(session, email))[-1] == "inference.refused"


async def test_five_emails_are_processed_together(
    app: FastAPI, session_factory: Db, fake_llm: FakeLLM, database_url: str
) -> None:
    """NFR throughput: five concurrent emails, no errors, and one unbroken audit chain."""
    if database_url.startswith("sqlite"):
        # Concurrent appends to the chain are serialised by a PostgreSQL advisory lock.
        # SQLite has no equivalent, so there the unique prev_hash constraint rejects one
        # of two racing appends instead. CI runs this test on PostgreSQL.
        pytest.skip("needs PostgreSQL: concurrent audit appends")
    services: Services = app.state.services
    async with services.session_factory() as session:
        for number in range(5):
            await ingest_email(
                session, services.bus, services.cipher, incoming_email(f"Bericht {number}.")
            )
    await services.bus.join()

    async with session_factory() as session:
        statuses = (await session.scalars(select(Email.status))).all()
        chain = await verify_chain(session)
    assert statuses == [EmailStatus.DRAFT_READY] * 5
    assert len(fake_llm.calls) == 5
    assert chain.ok
    assert chain.checked == 20
