import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import writer as audit
from app.db.models import PiiMapping, RedactionRecord
from app.inference.clients import ChatClient
from app.inference.prompts import render_prompt
from app.privacy.crypto import Cipher
from app.privacy.redaction import RedactedText

logger = structlog.get_logger(__name__)


class UnredactedInputError(Exception):
    """The router refused a model call. Never put the offending text in the message."""


@dataclass(frozen=True)
class InferenceResult:
    text: str
    model: str
    left_local: bool


class InferenceRouter:
    """The only way to reach a model (CLAUDE.md rule 1, FR-14).

    Callers hand over a prompt name and redacted variables, not a finished prompt, so the
    router can check exactly the text that came from the email.
    """

    def __init__(self, *, local: ChatClient, cipher: Cipher) -> None:
        self._local = local
        self._cipher = cipher

    async def complete(
        self,
        session: AsyncSession,
        *,
        email_id: uuid.UUID,
        actor: str,
        prompt_name: str,
        variables: Mapping[str, RedactedText],
    ) -> InferenceResult:
        await self._require_redacted(session, email_id, variables)
        prompt = render_prompt(prompt_name, variables)

        started = time.perf_counter()
        completion = await self._local.complete(system=prompt.system, user=prompt.user)
        left_local = False  # the cloud route is added later; until then nothing leaves

        await audit.record(
            session,
            actor=actor,
            action="inference.completed",
            email_id=email_id,
            model=completion.model,
            left_local=left_local,
        )
        logger.info(
            "inference.completed",
            prompt=prompt_name,
            model=completion.model,
            left_local=left_local,
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
            duration_ms=round((time.perf_counter() - started) * 1000),
        )
        return InferenceResult(text=completion.text, model=completion.model, left_local=left_local)

    async def _require_redacted(
        self, session: AsyncSession, email_id: uuid.UUID, variables: Mapping[str, RedactedText]
    ) -> None:
        has_record = await session.scalar(
            select(RedactionRecord.id).where(RedactionRecord.email_id == email_id)
        )
        if has_record is None:
            raise UnredactedInputError("no redaction record for this email")

        # Defence in depth: the type says "redacted", this checks it. If any value the
        # privacy agent replaced is present, the caller passed the wrong text.
        encrypted_values = await session.scalars(
            select(PiiMapping.encrypted_value).where(PiiMapping.email_id == email_id)
        )
        originals = [self._cipher.decrypt(value) for value in encrypted_values]
        for text in variables.values():
            if any(original in text for original in originals):
                raise UnredactedInputError("input contains a value that should be redacted")
