import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditEvent, PiiMapping, RedactionRecord
from app.inference.router import InferenceRouter, UnredactedInputError
from app.privacy.crypto import Cipher
from app.privacy.entities import EntityType
from app.privacy.redaction import RedactedText
from tests.helpers import BSN, FAKE_MODEL, FakeLLM, incoming_email, store_email


async def _complete(
    router: InferenceRouter, session: AsyncSession, email_id: object, body: str
) -> object:
    return await router.complete(
        session,
        email_id=email_id,  # type: ignore[arg-type]
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
