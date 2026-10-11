import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Email, User
from app.inference.clients import Completion
from app.orchestrator.ingestion import IncomingEmail
from app.privacy.crypto import Cipher
from app.privacy.original import OriginalEmail

FAKE_MODEL = "fake-model"
# How each agent's system prompt starts (prompts/*.md).
INTAKE_PROMPT = "Je sorteert"
DRAFT_PROMPT = "Je bent een medewerker"
QUALITY_PROMPT = "Je controleert"


class FakeLLM:
    """Stands in for the model. Records every prompt it is given, so tests can assert
    on exactly what would have reached a real one.

    It answers each agent's prompt in that agent's format; tests change the attributes
    to steer what the "model" says."""

    def __init__(self, model: str = FAKE_MODEL) -> None:
        self.model = model
        self.calls: list[tuple[str, str]] = []
        self.reply = (
            "Geachte [PERSON_1],\n\nUw BSN [BSN_1] is bekend.\n\nMet vriendelijke groet,\nGemeente"
        )
        self.sources_used: list[int] = []
        # "other" has no team of its own, so the email stays with the team that has it.
        self.classification: dict[str, object] = {
            "topic": "other",
            "urgency": "normal",
            "sensitive": False,
        }
        self.quality_warnings: list[dict[str, str]] = []
        # Returned verbatim instead of a well-formed answer, when set.
        self.raw: str | None = None
        self.error: Exception | None = None

    def calls_for(self, prompt_start: str) -> list[tuple[str, str]]:
        return [call for call in self.calls if call[0].startswith(prompt_start)]

    def _answer(self, system: str, json_mode: bool) -> str:
        if self.raw is not None:
            return self.raw
        if not json_mode:
            return self.reply
        if system.startswith(INTAKE_PROMPT):
            return json.dumps(self.classification)
        if system.startswith(QUALITY_PROMPT):
            return json.dumps({"warnings": self.quality_warnings})
        return json.dumps({"reply": self.reply, "sources_used": self.sources_used})

    async def complete(self, *, system: str, user: str, json_mode: bool = False) -> Completion:
        self.calls.append((system, user))
        if self.error is not None:
            raise self.error
        return Completion(
            text=self._answer(system, json_mode),
            model=self.model,
            prompt_tokens=1,
            completion_tokens=1,
        )


class FakeEmbedder:
    """Every text gets the same vector unless a test says otherwise, so anything in the
    knowledge base counts as similar. Records what it was asked to embed."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.vectors: dict[str, list[float]] = {}
        self.default = [1.0, 0.0]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self.vectors.get(text, self.default) for text in texts]


def valid_bsn(first_eight: str = "12345678") -> str:
    """Derive a number that passes the 11-check instead of hardcoding one."""
    partial = sum(int(d) * w for d, w in zip(first_eight, (9, 8, 7, 6, 5, 4, 3, 2), strict=True))
    assert partial % 11 < 10, "pick other digits"
    return first_eight + str(partial % 11)


BSN = valid_bsn()
SENDER = "inwoner@example.org"
POSTCODE = "1234 AB"
IBAN = "NL91ABNA0417164300"  # the documentation example IBAN


def incoming_email(body_extra: str = "") -> IncomingEmail:
    return IncomingEmail(
        sender=SENDER,
        subject="Vraag over mijn aanvraag",
        body=(
            "Geachte heer/mevrouw,\n\n"
            f"Mijn BSN is {BSN} en u kunt mij mailen op {SENDER}. "
            f"Ik woon op postcode {POSTCODE}. Mijn rekeningnummer is {IBAN}.\n"
            f"{body_extra}\n"
            "Met vriendelijke groet,\nEen inwoner"
        ),
        received_at=datetime(2026, 10, 1, 9, 0, tzinfo=UTC),
    )


async def store_email(session: AsyncSession, cipher: Cipher, incoming: IncomingEmail) -> Email:
    """An email in the database that no agent has touched yet."""
    email = Email(
        id=incoming.id,
        correlation_id=uuid.uuid4(),
        original_encrypted=OriginalEmail(incoming.sender, incoming.subject, incoming.body).encrypt(
            cipher
        ),
        received_at=incoming.received_at,
    )
    session.add(email)
    await session.flush()
    return email


@asynccontextmanager
async def logged_in(app: FastAPI, user: User | None) -> AsyncIterator[httpx.AsyncClient]:
    """A browser session for `user`, or an anonymous one for None."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        if user is not None:
            response = await client.post(f"/login/{user.id}")
            assert response.status_code == 303
        yield client
