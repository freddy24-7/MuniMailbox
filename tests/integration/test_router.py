import asyncio
import uuid
from collections.abc import Awaitable

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.intake import Classification
from app.db.models import AuditEvent, PiiMapping, RedactionRecord
from app.inference.clients import Completion
from app.inference.router import (
    InferenceResult,
    InferenceRouter,
    InvalidModelOutputError,
    UnredactedInputError,
)
from app.privacy.crypto import Cipher
from app.privacy.entities import EntityType
from app.privacy.redaction import RedactedText
from tests.helpers import BSN, FAKE_MODEL, FakeEmbedder, FakeLLM, incoming_email, store_email


async def _complete(
    router: InferenceRouter, session: AsyncSession, email_id: uuid.UUID, body: str
) -> InferenceResult[str]:
    return await router.complete(
        session,
        email_id=email_id,
        actor="agent:test",
        prompt_name="draft_reply.v1",
        variables={"subject": RedactedText("Vraag"), "body": RedactedText(body)},
    )


async def test_refuses_an_email_without_a_redaction_record(
    session: AsyncSession, cipher: Cipher, fake_llm: FakeLLM
) -> None:
    """FR-14: the rule that cannot be bypassed. The model is never called."""
    email = await store_email(session, cipher, incoming_email())
    router = InferenceRouter(local=fake_llm, cipher=cipher)

    with pytest.raises(UnredactedInputError):
        await _complete(router, session, email.id, "Tekst met [BSN_1]")

    assert fake_llm.calls == []


async def test_refuses_text_that_still_contains_a_redacted_value(
    session: AsyncSession, cipher: Cipher, fake_llm: FakeLLM
) -> None:
    """A redaction record exists, but the caller passed the original text anyway."""
    email = await store_email(session, cipher, incoming_email())
    session.add(RedactionRecord(email_id=email.id, entity_counts={"BSN": 1}))
    session.add(
        PiiMapping(
            email_id=email.id,
            placeholder="[BSN_1]",
            encrypted_value=cipher.encrypt(BSN),
            entity_type=EntityType.BSN,
        )
    )
    await session.flush()
    router = InferenceRouter(local=fake_llm, cipher=cipher)

    with pytest.raises(UnredactedInputError) as refusal:
        await _complete(router, session, email.id, f"Mijn BSN is {BSN}")

    assert fake_llm.calls == []
    assert BSN not in str(refusal.value)  # the error is logged; it must not carry the value


async def test_redacted_input_is_sent_and_audited(
    session: AsyncSession, cipher: Cipher, fake_llm: FakeLLM
) -> None:
    email = await store_email(session, cipher, incoming_email())
    session.add(RedactionRecord(email_id=email.id, entity_counts={}))
    await session.flush()
    router = InferenceRouter(local=fake_llm, cipher=cipher)

    await _complete(router, session, email.id, "Mijn BSN is [BSN_1]")

    ((_, user_prompt),) = fake_llm.calls
    assert "Mijn BSN is [BSN_1]" in user_prompt
    event = (await session.scalars(select(AuditEvent))).one()
    assert (event.action, event.model, event.left_local) == (
        "inference.completed",
        FAKE_MODEL,
        False,
    )


# --- Cloud route (FR-13) and embeddings ---

CLOUD_MODEL = "fake-cloud-model"


async def _redacted_email(session: AsyncSession, cipher: Cipher) -> uuid.UUID:
    """An email whose BSN the privacy agent has replaced."""
    email = await store_email(session, cipher, incoming_email())
    session.add(RedactionRecord(email_id=email.id, entity_counts={"BSN": 1}))
    session.add(
        PiiMapping(
            email_id=email.id,
            placeholder="[BSN_1]",
            encrypted_value=cipher.encrypt(BSN),
            entity_type=EntityType.BSN,
        )
    )
    await session.flush()
    return email.id


async def _audit(session: AsyncSession) -> list[tuple[str, str | None, bool | None]]:
    events = await session.scalars(select(AuditEvent).order_by(AuditEvent.id))
    return [(event.action, event.model, event.left_local) for event in events]


async def test_without_a_cloud_model_nothing_leaves_and_a_local_failure_is_a_failure(
    session: AsyncSession, cipher: Cipher, fake_llm: FakeLLM
) -> None:
    email_id = await _redacted_email(session, cipher)
    fake_llm.error = RuntimeError("model is down")
    router = InferenceRouter(local=fake_llm, cipher=cipher, cloud_route_min_chars=0)

    with pytest.raises(RuntimeError):
        await _complete(router, session, email_id, "Mijn BSN is [BSN_1]")


async def test_a_failing_local_model_falls_back_to_the_cloud_and_the_audit_says_so(
    session: AsyncSession, cipher: Cipher, fake_llm: FakeLLM
) -> None:
    email_id = await _redacted_email(session, cipher)
    fake_llm.error = RuntimeError("model is down")
    cloud = FakeLLM(model=CLOUD_MODEL)
    router = InferenceRouter(local=fake_llm, cloud=cloud, cloud_route_min_chars=6000, cipher=cipher)

    result = await _complete(router, session, email_id, "Mijn BSN is [BSN_1]")

    assert (result.model, result.left_local) == (CLOUD_MODEL, True)
    assert len(cloud.calls) == 1
    assert await _audit(session) == [
        ("inference.local_failed", None, False),
        ("inference.completed", CLOUD_MODEL, True),
    ]


async def test_a_large_request_goes_straight_to_the_cloud(
    session: AsyncSession, cipher: Cipher, fake_llm: FakeLLM
) -> None:
    email_id = await _redacted_email(session, cipher)
    cloud = FakeLLM(model=CLOUD_MODEL)
    router = InferenceRouter(local=fake_llm, cloud=cloud, cloud_route_min_chars=50, cipher=cipher)

    small = await _complete(router, session, email_id, "Kort bericht van [BSN_1]")
    large = await _complete(router, session, email_id, "Lang bericht. " * 10)

    assert (small.left_local, large.left_local) == (False, True)
    assert (len(fake_llm.calls), len(cloud.calls)) == (1, 1)


@pytest.mark.parametrize("local_is_down", [False, True])
async def test_the_cloud_route_refuses_unredacted_text_exactly_like_the_local_one(
    session: AsyncSession, cipher: Cipher, fake_llm: FakeLLM, local_is_down: bool
) -> None:
    """The bypass test for FR-14 on the route where a leak would actually leave the
    building: neither size-based routing nor the fallback gets around the check."""
    email_id = await _redacted_email(session, cipher)
    if local_is_down:
        fake_llm.error = RuntimeError("model is down")
    cloud = FakeLLM(model=CLOUD_MODEL)
    router = InferenceRouter(local=fake_llm, cloud=cloud, cloud_route_min_chars=0, cipher=cipher)

    with pytest.raises(UnredactedInputError):
        await _complete(router, session, email_id, f"Mijn BSN is {BSN}")

    assert fake_llm.calls == []
    assert cloud.calls == []


async def test_the_cloud_route_refuses_an_email_without_a_redaction_record(
    session: AsyncSession, cipher: Cipher, fake_llm: FakeLLM
) -> None:
    email = await store_email(session, cipher, incoming_email())
    cloud = FakeLLM(model=CLOUD_MODEL)
    router = InferenceRouter(local=fake_llm, cloud=cloud, cloud_route_min_chars=0, cipher=cipher)

    with pytest.raises(UnredactedInputError):
        await _complete(router, session, email.id, "Tekst met [BSN_1]")

    assert cloud.calls == []


async def test_an_answer_that_does_not_fit_the_schema_falls_back_like_a_timeout(
    session: AsyncSession, cipher: Cipher, fake_llm: FakeLLM
) -> None:
    email_id = await _redacted_email(session, cipher)
    fake_llm.raw = f"Dat weet ik niet, maar het BSN was {BSN}"
    cloud = FakeLLM(model=CLOUD_MODEL)
    variables = {"subject": RedactedText("Vraag"), "body": RedactedText("Tekst [BSN_1]")}

    def ask(router: InferenceRouter) -> Awaitable[InferenceResult[Classification]]:
        return router.complete_json(
            session,
            email_id=email_id,
            actor="agent:test",
            prompt_name="intake.v1",
            variables=variables,
            schema=Classification,
        )

    with pytest.raises(InvalidModelOutputError) as invalid:
        await ask(InferenceRouter(local=fake_llm, cipher=cipher))
    assert BSN not in str(invalid.value)  # the error is logged; it must not quote the model

    result = await ask(InferenceRouter(local=fake_llm, cloud=cloud, cipher=cipher))
    assert (result.output.topic.value, result.left_local) == ("other", True)


async def test_embedding_email_text_is_guarded_like_any_other_model_call(
    session: AsyncSession, cipher: Cipher, fake_llm: FakeLLM, fake_embedder: FakeEmbedder
) -> None:
    unredacted = await store_email(session, cipher, incoming_email())
    email_id = await _redacted_email(session, cipher)
    router = InferenceRouter(local=fake_llm, embedder=fake_embedder, cipher=cipher)

    with pytest.raises(UnredactedInputError):
        await router.embed_query(session, email_id=unredacted.id, text=RedactedText("Vraag"))
    with pytest.raises(UnredactedInputError):
        await router.embed_query(session, email_id=email_id, text=RedactedText(f"BSN {BSN}"))
    assert fake_embedder.calls == []

    assert await router.embed_query(
        session, email_id=email_id, text=RedactedText("BSN [BSN_1]")
    ) == [1.0, 0.0]
    assert fake_embedder.calls == [["BSN [BSN_1]"]]


# --- One local machine: a limit on calls at the same time ---


class _InFlight:
    """Counts how many calls are inside a model at the same moment."""

    def __init__(self) -> None:
        self.now = 0
        self.most = 0

    async def hold(self) -> None:
        self.now += 1
        self.most = max(self.most, self.now)
        await asyncio.sleep(0.01)
        self.now -= 1


class _SlowLLM(FakeLLM):
    def __init__(self, in_flight: _InFlight) -> None:
        super().__init__()
        self._in_flight = in_flight

    async def complete(self, *, system: str, user: str, json_mode: bool = False) -> Completion:
        await self._in_flight.hold()
        return await super().complete(system=system, user=user, json_mode=json_mode)


class _SlowEmbedder(FakeEmbedder):
    def __init__(self, in_flight: _InFlight) -> None:
        super().__init__()
        self._in_flight = in_flight

    async def embed(self, texts: list[str]) -> list[list[float]]:
        await self._in_flight.hold()
        return await super().embed(texts)


@pytest.mark.parametrize("limit", [1, 2])
async def test_chat_and_embedding_calls_share_the_limit_on_the_local_server(
    session: AsyncSession, cipher: Cipher, limit: int
) -> None:
    email_id = await _redacted_email(session, cipher)
    in_flight = _InFlight()
    router = InferenceRouter(
        local=_SlowLLM(in_flight),
        embedder=_SlowEmbedder(in_flight),
        local_max_concurrency=limit,
        cipher=cipher,
    )

    await asyncio.gather(
        _complete(router, session, email_id, "Mijn BSN is [BSN_1]"),
        *(router.embed_public([f"Openbare tekst {number}"]) for number in range(4)),
    )

    assert in_flight.most == limit
