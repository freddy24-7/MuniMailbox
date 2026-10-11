import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.base import EmailContext
from app.agents.drafting import DraftingAgent
from app.agents.intake import IntakeAgent
from app.agents.policy import PolicyAgent
from app.agents.privacy import PrivacyAgent
from app.agents.quality import QualityAgent
from app.audit.writer import verify_chain
from app.db.models import AuditEvent, Draft, Email, EmailStatus, PiiMapping
from app.events.bus import InMemoryEventBus
from app.inference.clients import Completion
from app.inference.router import InferenceRouter, UnredactedInputError
from app.orchestrator.ingestion import IncomingEmail, ingest_email
from app.orchestrator.workflow import Orchestrator
from app.privacy.crypto import Cipher
from app.privacy.redaction import Redactor
from app.rag.retrieval import Retriever
from app.rag.store import InMemoryVectorStore
from app.services import Services
from tests.helpers import (
    BSN,
    IBAN,
    POSTCODE,
    SENDER,
    FakeEmbedder,
    FakeLLM,
    incoming_email,
    store_email,
)

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
    app: FastAPI, session_factory: Db, fake_llm: FakeLLM, fake_embedder: FakeEmbedder
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
    # Policy and case run at the same time on PostgreSQL, so their order is not fixed.
    assert actions[:4] == [
        "email.received",
        "privacy.completed",
        "inference.completed",
        "intake.completed",
    ]
    assert sorted(actions[4:6]) == ["case.completed", "policy.completed"]
    assert actions[6:] == [
        "inference.completed",
        "drafting.completed",
        "inference.completed",
        "quality.completed",
    ]
    assert chain.ok

    # Acceptance criterion: the stored draft and all LLM inputs contain placeholders only.
    assert [system.split()[1] for system, _ in fake_llm.calls] == [
        "sorteert",
        "bent",
        "controleert",
    ]
    assert len(fake_embedder.calls) == 1
    model_inputs = [text for call in fake_llm.calls for text in call]
    model_inputs += [text for call in fake_embedder.calls for text in call]
    assert all("[BSN_1]" in user_prompt for _, user_prompt in fake_llm.calls)
    for value in PERSONAL_VALUES:
        assert all(value not in text for text in model_inputs)
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
        # FR-3: tried, retried twice, and only then handed to a human.
        assert (await _actions(session, email))[-3:] == [
            "intake.retried",
            "intake.retried",
            "intake.failed",
        ]
    assert len(fake_llm.calls) == 3


class _FailsOnce(FakeLLM):
    async def complete(self, *, system: str, user: str, json_mode: bool = False) -> Completion:
        completion = await super().complete(system=system, user=user, json_mode=json_mode)
        if len(self.calls) == 1:
            raise RuntimeError("model hiccup")
        return completion


async def test_a_model_that_recovers_on_retry_still_produces_a_draft(
    session_factory: Db, cipher: Cipher, redactor: Redactor
) -> None:
    llm = _FailsOnce()
    router = InferenceRouter(local=llm, cipher=cipher)
    bus = InMemoryEventBus(workers=1)
    Orchestrator(
        bus=bus,
        session_factory=session_factory,
        steps=[PrivacyAgent(redactor=redactor, cipher=cipher), DraftingAgent(router=router)],
        max_retries=1,
    ).register()
    await bus.start()
    async with session_factory() as session:
        email = await ingest_email(session, bus, cipher, incoming_email())
    await bus.join()
    await bus.stop()

    async with session_factory() as session:
        stored = await session.get_one(Email, email.id)
        assert stored.status is EmailStatus.DRAFT_READY
        assert len((await session.scalars(select(Draft))).all()) == 1  # not one per attempt
        assert await _actions(session, email) == [
            "email.received",
            "privacy.completed",
            "drafting.retried",
            "inference.completed",
            "drafting.completed",
        ]
        assert (await verify_chain(session)).ok


async def test_output_that_does_not_fit_the_schema_is_a_failure_not_a_draft(
    app: FastAPI, session_factory: Db, fake_llm: FakeLLM
) -> None:
    """FR-6: the intake model may only answer with a known topic."""
    fake_llm.classification = {"topic": "taxes", "urgency": "normal", "sensitive": False}
    email = await _ingest(app, incoming_email())

    async with session_factory() as session:
        stored = await session.get_one(Email, email.id)
        assert (stored.status, stored.topic) == (EmailStatus.NEEDS_ATTENTION, None)
        assert (await _actions(session, email))[-1] == "intake.failed"


async def test_a_workflow_that_skips_the_privacy_agent_never_reaches_the_model(
    session_factory: Db,
    cipher: Cipher,
    fake_llm: FakeLLM,
    fake_embedder: FakeEmbedder,
    vector_store: InMemoryVectorStore,
) -> None:
    """The bypass test for CLAUDE.md rule 1: build the pipeline wrongly on purpose."""
    router = InferenceRouter(local=fake_llm, embedder=fake_embedder, cipher=cipher)
    bus = InMemoryEventBus(workers=1)
    Orchestrator(
        bus=bus,
        session_factory=session_factory,
        steps=[
            IntakeAgent(router=router, team_by_topic={}),
            PolicyAgent(
                router=router, retriever=Retriever(vector_store, top_k=4, min_similarity=0.5)
            ),
            DraftingAgent(router=router),
            QualityAgent(router=router),
        ],
        max_retries=2,
    ).register()
    await bus.start()
    async with session_factory() as session:
        email = await ingest_email(session, bus, cipher, incoming_email())
    await bus.join()
    await bus.stop()

    assert fake_llm.calls == []
    assert fake_embedder.calls == []
    async with session_factory() as session:
        stored = await session.get_one(Email, email.id)
        assert stored.status is EmailStatus.NEEDS_ATTENTION
        # Refused at the first agent, and not retried: the answer would be the same.
        assert await _actions(session, email) == ["email.received", "inference.refused"]


@pytest.mark.parametrize("agent", ["policy", "drafting", "quality"])
async def test_every_agent_that_reaches_a_model_is_refused_without_redaction(
    session_factory: Db,
    cipher: Cipher,
    fake_llm: FakeLLM,
    fake_embedder: FakeEmbedder,
    vector_store: InMemoryVectorStore,
    agent: str,
) -> None:
    """Rule 1 holds for each agent on its own, not only for whichever runs first."""
    router = InferenceRouter(local=fake_llm, embedder=fake_embedder, cipher=cipher)
    agents = {
        "policy": PolicyAgent(
            router=router, retriever=Retriever(vector_store, top_k=4, min_similarity=0.5)
        ),
        "drafting": DraftingAgent(router=router),
        "quality": QualityAgent(router=router),
    }
    async with session_factory() as session:
        email = await store_email(session, cipher, incoming_email())
        session.add(Draft(email_id=email.id, text_redacted="Tekst", model_used="none"))
        await session.flush()

        with pytest.raises(UnredactedInputError):
            await agents[agent].run(EmailContext(email.id, email.correlation_id, session))

    assert fake_llm.calls == []
    assert fake_embedder.calls == []


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
    assert len(fake_llm.calls) == 15
    assert chain.ok
    assert chain.checked == 50
