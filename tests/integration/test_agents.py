"""What each agent after privacy contributes, seen through the whole pipeline."""

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.agents.base import EmailContext
from app.agents.quality import QualityAgent
from app.config import Settings
from app.db.models import (
    AuditEvent,
    Case,
    CaseStatus,
    Citizen,
    Draft,
    Email,
    EmailStatus,
    PiiMapping,
    Role,
    Topic,
    Urgency,
    User,
)
from app.inference.clients import Completion
from app.inference.router import InferenceRouter, UnredactedInputError
from app.main import create_app
from app.orchestrator.ingestion import IncomingEmail, ingest_email
from app.privacy.crypto import Cipher
from app.privacy.redaction import Redactor
from app.rag.chunking import Document
from app.rag.ingestion import ingest
from app.rag.store import InMemoryVectorStore
from app.services import Services
from tests.conftest import TEAM
from tests.helpers import (
    BSN,
    DRAFT_PROMPT,
    QUALITY_PROMPT,
    SENDER,
    FakeEmbedder,
    FakeLLM,
    incoming_email,
    logged_in,
    store_email,
    valid_bsn,
)

Db = async_sessionmaker[AsyncSession]
Users = dict[Role, User]

SOURCE_URL = "https://gemeente.example/afval/grofvuil"
OTHER_PERSON = "Sanne de Vries"
OTHER_BSN = valid_bsn("23456789")


async def _run(app: FastAPI, incoming: IncomingEmail | None = None) -> Email:
    services: Services = app.state.services
    async with services.session_factory() as session:
        email = await ingest_email(
            session, services.bus, services.cipher, incoming or incoming_email(), team=TEAM
        )
    await services.bus.join()
    return email


async def _fill_knowledge_base(store: InMemoryVectorStore, embedder: FakeEmbedder) -> None:
    document = Document(
        url=SOURCE_URL,
        title="Grofvuil",
        topic="waste_collection",
        fetched_at="2026-10-01",
        text="# Grofvuil\nMaak een afspraak.\n## Kosten\nOphalen kost € 25.",
    )
    await ingest([document], store, embedder.embed, max_chars=1200)
    embedder.calls.clear()


async def _stored(session_factory: Db, email: Email) -> tuple[Email, Draft]:
    async with session_factory() as session:
        return (
            await session.get_one(Email, email.id),
            (await session.scalars(select(Draft).where(Draft.email_id == email.id))).one(),
        )


# --- Intake (FR-6) ---


async def test_intake_classifies_and_routes_to_the_team_for_the_topic(
    app: FastAPI, session_factory: Db, fake_llm: FakeLLM
) -> None:
    fake_llm.classification = {"topic": "benefits", "urgency": "high", "sensitive": True}
    email = await _run(app)

    stored, _ = await _stored(session_factory, email)
    assert (stored.topic, stored.urgency, stored.sensitive) == (Topic.BENEFITS, Urgency.HIGH, True)
    assert stored.team == "Werk en Inkomen"


async def test_a_topic_without_a_team_stays_where_it_is(
    app: FastAPI, session_factory: Db, fake_llm: FakeLLM
) -> None:
    fake_llm.classification = {"topic": "complaints", "urgency": "normal", "sensitive": False}
    email = await _run(app)

    stored, _ = await _stored(session_factory, email)
    assert (stored.topic, stored.team) == (Topic.COMPLAINTS, TEAM)


# --- Policy and drafting (FR-7, FR-10, FR-17) ---


async def test_the_draft_is_written_from_retrieved_sources_and_cites_them(
    app: FastAPI,
    session_factory: Db,
    fake_llm: FakeLLM,
    fake_embedder: FakeEmbedder,
    vector_store: InMemoryVectorStore,
) -> None:
    await _fill_knowledge_base(vector_store, fake_embedder)
    fake_llm.sources_used = [2]
    email = await _run(app)

    stored, draft = await _stored(session_factory, email)
    assert [source["id"] for source in stored.policy_context or []] == [1, 2]
    ((_, draft_prompt),) = fake_llm.calls_for(DRAFT_PROMPT)
    assert "[2] Grofvuil - Kosten\nOphalen kost € 25." in draft_prompt
    assert SOURCE_URL not in draft_prompt  # the model cites a number; links are ours
    assert draft.sources == [
        {
            "id": 2,
            "title": "Grofvuil",
            "heading": "Kosten",
            "url": SOURCE_URL,
            "fetched_at": "2026-10-01",
        }
    ]
    assert draft.warnings == []


async def test_a_source_the_model_was_not_given_is_dropped_and_reported(
    app: FastAPI,
    session_factory: Db,
    fake_llm: FakeLLM,
    fake_embedder: FakeEmbedder,
    vector_store: InMemoryVectorStore,
) -> None:
    """Never let the model invent sources (CLAUDE.md coding conventions)."""
    await _fill_knowledge_base(vector_store, fake_embedder)
    fake_llm.sources_used = [1, 7, 1]
    email = await _run(app)

    _, draft = await _stored(session_factory, email)
    assert [source["id"] for source in draft.sources] == [1]
    assert [(w["type"], w["origin"]) for w in draft.warnings] == [("invalid_citation", "rule")]
    assert "source 7" in draft.warnings[0]["message"]


async def test_retrieval_stays_inside_the_topic_the_intake_agent_chose(
    app: FastAPI,
    session_factory: Db,
    fake_llm: FakeLLM,
    fake_embedder: FakeEmbedder,
    vector_store: InMemoryVectorStore,
) -> None:
    await _fill_knowledge_base(vector_store, fake_embedder)
    fake_llm.classification = {"topic": "permits", "urgency": "normal", "sensitive": False}
    email = await _run(app)

    stored, draft = await _stored(session_factory, email)
    assert stored.policy_context == []
    assert "(geen bronnen gevonden)" in fake_llm.calls_for(DRAFT_PROMPT)[0][1]
    assert [w["type"] for w in draft.warnings] == ["no_source"]


# --- Quality (FR-11) ---


async def test_quality_warnings_inform_the_caseworker_and_block_nothing(
    app: FastAPI,
    session_factory: Db,
    users: Users,
    fake_llm: FakeLLM,
    fake_embedder: FakeEmbedder,
    vector_store: InMemoryVectorStore,
) -> None:
    await _fill_knowledge_base(vector_store, fake_embedder)
    fake_llm.sources_used = [2]
    fake_llm.reply = (
        "Geachte [PERSON_9],\n\nOphalen kost € 40.\n\nMet vriendelijke groet,\nGemeente"
    )
    fake_llm.quality_warnings = [{"type": "tone", "message": "De toon is te kortaf."}]
    email = await _run(app)

    stored, draft = await _stored(session_factory, email)
    assert stored.status is EmailStatus.DRAFT_READY
    assert [(w["type"], w["origin"]) for w in draft.warnings] == [
        ("unknown_placeholder", "rule"),
        ("unsupported_number", "rule"),
        ("tone", "model"),
    ]
    # The checker sees the draft and the same sources, and nothing unredacted.
    ((_, quality_prompt),) = fake_llm.calls_for(QUALITY_PROMPT)
    assert "Ophalen kost € 40." in quality_prompt
    assert "[2] Grofvuil - Kosten" in quality_prompt
    assert BSN not in quality_prompt

    async with logged_in(app, users[Role.CASEWORKER]) as client:
        page = (await client.get(f"/emails/{email.id}")).text
    assert "De toon is te kortaf." in page
    assert "appears in the reply but in none of the sources" in page
    assert f'href="{SOURCE_URL}"' in page
    assert "Approve and send" in page


# --- Case history (FR-8) ---


async def _citizen_with_case(session_factory: Db, summary: str) -> Citizen:
    citizen = Citizen(
        name="Test Inwoner",
        address="Voorbeeldstraat 1, 1234 AB Voorbeeldstad",
        bsn=BSN,
        email=SENDER,
        phone="06-12345678",
    )
    async with session_factory() as session:
        session.add(citizen)
        await session.flush()
        session.add(
            Case(
                citizen_id=citizen.id,
                topic=Topic.PERMITS,
                status=CaseStatus.OPEN,
                opened_at=datetime(2026, 8, 3, tzinfo=UTC),
                summary=summary,
            )
        )
        await session.commit()
    return citizen


async def test_case_history_reaches_the_model_redacted_and_the_caseworker_restored(
    app: FastAPI, session_factory: Db, users: Users, fake_llm: FakeLLM
) -> None:
    """Case summaries are free text from the case system: they name the sender and can
    name other people too. None of that may reach the model."""
    citizen = await _citizen_with_case(
        session_factory,
        f"Aanvraag dakkapel van {OTHER_PERSON}, BSN {OTHER_BSN}, namens Test Inwoner ({BSN}).",
    )
    email = await _run(app)

    stored, _ = await _stored(session_factory, email)
    (case,) = stored.case_context or []
    assert (case["topic"], case["status"], case["opened"]) == ("permits", "open", "2026-08")
    summary = case["summary_redacted"]
    for value in (BSN, OTHER_BSN, citizen.name, "Vries"):
        assert value not in summary
    # The sender's BSN keeps the placeholder it has in the email; the new one gets the next.
    assert "[BSN_1]" in summary
    assert "[BSN_2]" in summary

    ((_, draft_prompt),) = fake_llm.calls_for(DRAFT_PROMPT)
    assert summary in draft_prompt
    for system, user in fake_llm.calls:
        for value in (BSN, OTHER_BSN, citizen.name, "Vries"):
            assert value not in system + user

    async with session_factory() as session:
        placeholders = list(
            await session.scalars(
                select(PiiMapping.placeholder).where(PiiMapping.email_id == email.id)
            )
        )
    assert len(placeholders) == len(set(placeholders))  # extended, not duplicated

    async with logged_in(app, users[Role.CASEWORKER]) as client:
        page = (await client.get(f"/emails/{email.id}")).text
    assert f"BSN {OTHER_BSN}" in page  # restored for the role that may see it


async def test_an_unknown_sender_has_no_case_history(
    app: FastAPI, session_factory: Db, fake_llm: FakeLLM
) -> None:
    email = await _run(app)

    stored, _ = await _stored(session_factory, email)
    assert stored.case_context == []
    assert "(geen eerdere zaken bekend)" in fake_llm.calls_for(DRAFT_PROMPT)[0][1]


class _QualityModelIsDown(FakeLLM):
    async def complete(self, *, system: str, user: str, json_mode: bool = False) -> Completion:
        if system.startswith(QUALITY_PROMPT):
            self.calls.append((system, user))
            raise RuntimeError("model is down")
        return await super().complete(system=system, user=user, json_mode=json_mode)


async def test_a_failing_quality_check_keeps_the_draft_and_says_it_was_not_checked(
    settings: Settings,
    engine: AsyncEngine,
    session_factory: Db,
    fake_embedder: FakeEmbedder,
    vector_store: InMemoryVectorStore,
    redactor: Redactor,
) -> None:
    """The check is advisory: losing it must not cost the caseworker a usable draft."""
    llm = _QualityModelIsDown()
    llm.reply = "Geachte [PERSON_9],\n\nMet vriendelijke groet,\nGemeente"
    app = create_app(
        settings,
        local_llm=llm,
        embedder=fake_embedder,
        vector_store=vector_store,
        redactor=redactor,
    )
    async with app.router.lifespan_context(app):
        email = await _run(app)

    stored, draft = await _stored(session_factory, email)
    assert stored.status is EmailStatus.DRAFT_READY
    assert len(llm.calls_for(QUALITY_PROMPT)) == 3  # retried first, like any failure
    # The rules still ran; the missing model check is said out loud.
    assert [w["type"] for w in draft.warnings] == [
        "no_source",
        "unknown_placeholder",
        "not_checked",
    ]
    async with session_factory() as session:
        actions = list(
            await session.scalars(
                select(AuditEvent.action)
                .where(AuditEvent.email_id == email.id)
                .order_by(AuditEvent.id)
            )
        )
    assert actions[-4:] == [
        "quality.retried",
        "quality.retried",
        "quality.model_check_failed",
        "quality.completed",
    ]


async def test_a_refusal_is_never_downgraded_to_a_missing_check(
    session_factory: Db, cipher: Cipher, fake_llm: FakeLLM
) -> None:
    """Rule 1 outranks convenience: without a redaction record the quality agent fails,
    also on its last attempt."""
    agent = QualityAgent(router=InferenceRouter(local=fake_llm, cipher=cipher))
    async with session_factory() as session:
        email = await store_email(session, cipher, incoming_email())
        session.add(Draft(email_id=email.id, text_redacted="Tekst", model_used="none"))
        await session.flush()

        with pytest.raises(UnredactedInputError):
            await agent.run(
                EmailContext(email.id, email.correlation_id, session, final_attempt=True)
            )
    assert fake_llm.calls == []
