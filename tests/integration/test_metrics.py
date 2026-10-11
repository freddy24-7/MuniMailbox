"""Prometheus metrics (NFR-1): what is counted, and what must never be in them."""

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from prometheus_client import REGISTRY
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.audit import writer as audit
from app.config import Settings
from app.db.models import Email
from app.inference.router import InferenceRouter, UnredactedInputError
from app.main import create_app
from app.orchestrator.ingestion import ingest_email
from app.privacy.crypto import Cipher
from app.privacy.redaction import RedactedText, Redactor
from app.rag.store import InMemoryVectorStore
from app.services import Services
from tests.helpers import (
    BSN,
    FAKE_MODEL,
    SENDER,
    FakeEmbedder,
    FakeLLM,
    incoming_email,
    logged_in,
    store_email,
)

Db = async_sessionmaker[AsyncSession]
AGENTS = ("privacy", "intake", "policy", "case", "drafting", "quality")


class _Since:
    """How much a metric grew since this object was made. The registry is shared by all
    tests in the run, so only differences mean anything."""

    def __init__(self) -> None:
        self._before = self._snapshot()

    @staticmethod
    def _snapshot() -> dict[tuple[str, tuple[tuple[str, str], ...]], float]:
        return {
            (sample.name, tuple(sorted(sample.labels.items()))): sample.value
            for metric in REGISTRY.collect()
            for sample in metric.samples
        }

    def __call__(self, name: str, **labels: str) -> float:
        key = (f"munimailbox_{name}", tuple(sorted(labels.items())))
        return self._snapshot().get(key, 0.0) - self._before.get(key, 0.0)


async def _run_one(app: FastAPI) -> None:
    services: Services = app.state.services
    async with services.session_factory() as session:
        await ingest_email(session, services.bus, services.cipher, incoming_email())
    await services.bus.join()


async def test_one_email_through_the_pipeline_is_counted_at_every_step(app: FastAPI) -> None:
    grew = _Since()
    await _run_one(app)

    assert grew("emails_received_total") == 1
    assert grew("emails_processed_total", outcome="draft_ready") == 1
    assert grew("emails_processed_total", outcome="needs_attention") == 0
    assert grew("time_to_draft_seconds_count") == 1
    for agent in AGENTS:
        assert grew("agent_duration_seconds_count", agent=agent) == 1, agent
        assert grew("agent_failures_total", agent=agent) == 0, agent
    # Intake, drafting and quality call the model; all three stayed local.
    assert grew("inference_calls_total", route="local", model=FAKE_MODEL) == 3
    assert grew("inference_calls_total", route="cloud", model=FAKE_MODEL) == 0
    assert grew("inference_tokens_total", route="local", kind="prompt") == 3
    assert grew("redactions_per_email_count") == 1
    assert grew("redactions_total", entity_type="BSN") == 1
    assert grew("redactions_total", entity_type="IBAN") == 1


async def test_a_failing_model_shows_up_as_retries_a_failure_and_an_unprocessed_email(
    app: FastAPI, fake_llm: FakeLLM
) -> None:
    fake_llm.error = RuntimeError("model is down")
    grew = _Since()
    await _run_one(app)

    assert grew("agent_retries_total", agent="intake") == 2
    assert grew("agent_failures_total", agent="intake") == 1
    assert grew("emails_processed_total", outcome="needs_attention") == 1
    assert grew("emails_processed_total", outcome="draft_ready") == 0
    assert grew("inference_calls_total", route="local", model=FAKE_MODEL) == 0


async def test_a_refused_model_call_is_counted_as_a_privacy_event(
    session: AsyncSession, cipher: Cipher, fake_llm: FakeLLM
) -> None:
    email = await store_email(session, cipher, incoming_email())
    router = InferenceRouter(local=fake_llm, cipher=cipher)
    grew = _Since()

    with pytest.raises(UnredactedInputError):
        await router.complete(
            session,
            email_id=email.id,
            actor="agent:test",
            prompt_name="draft_reply.v1",
            variables={"subject": RedactedText("x"), "body": RedactedText("y")},
        )

    assert grew("inference_refusals_total", reason="no_redaction_record") == 1
    assert grew("inference_calls_total", route="local", model=FAKE_MODEL) == 0


async def test_the_metrics_page_has_numbers_and_nothing_about_anyone(
    app: FastAPI, session_factory: Db, cipher: Cipher
) -> None:
    await _run_one(app)
    async with logged_in(app, None) as client:  # Prometheus has no login
        page = await client.get("/metrics")

    assert page.status_code == 200
    assert 'munimailbox_emails_in_status{status="draft_ready"} 1.0' in page.text
    assert 'munimailbox_emails_in_status{status="processing"} 0.0' in page.text
    assert "munimailbox_cloud_calls_without_redaction_record 0.0" in page.text
    assert 'munimailbox_agent_duration_seconds_bucket{agent="drafting"' in page.text
    # No personal data, and no identifiers either: an ID per email as a label would
    # make one time series per email.
    services: Services = app.state.services
    async with session_factory() as session:
        stored = (await session.scalars(select(Email))).one()
    for forbidden in (BSN, SENDER, str(stored.id), str(stored.correlation_id)):
        assert forbidden not in page.text
    assert services.settings.metrics_token is None


async def test_a_cloud_call_for_an_email_without_a_redaction_record_is_visible(
    app: FastAPI, session_factory: Db, cipher: Cipher
) -> None:
    """The alert the PRD asks for. The number is computed from the audit trail and the
    redaction records, independently of the router that should have prevented it."""
    async with session_factory() as session:
        email = await store_email(session, cipher, incoming_email())
        await audit.record(
            session,
            actor="agent:rogue",
            action="inference.completed",
            email_id=email.id,
            model="some-cloud-model",
            left_local=True,
        )
        await session.commit()

    async with logged_in(app, None) as client:
        page = await client.get("/metrics")
    assert "munimailbox_cloud_calls_without_redaction_record 1.0" in page.text


@pytest.fixture
async def app_with_token(
    settings: Settings, engine: AsyncEngine, redactor: Redactor
) -> AsyncIterator[FastAPI]:
    protected = settings.model_copy(update={"metrics_token": SecretStr("scrape-secret")})
    app = create_app(
        protected,
        local_llm=FakeLLM(),
        embedder=FakeEmbedder(),
        vector_store=InMemoryVectorStore(),
        redactor=redactor,
    )
    async with app.router.lifespan_context(app):
        yield app


async def test_with_a_token_configured_the_metrics_page_requires_it(
    app_with_token: FastAPI,
) -> None:
    async with logged_in(app_with_token, None) as client:
        without = await client.get("/metrics")
        wrong = await client.get("/metrics", headers={"Authorization": "Bearer nope"})
        right = await client.get("/metrics", headers={"Authorization": "Bearer scrape-secret"})
    assert [without.status_code, wrong.status_code, right.status_code] == [401, 401, 200]
