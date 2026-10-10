import asyncio
from collections import Counter

from sqlalchemy import select

from app.agents.base import AgentResult, EmailContext
from app.db.models import Email, PiiMapping, RedactionRecord
from app.privacy.crypto import Cipher
from app.privacy.original import OriginalEmail
from app.privacy.redaction import Redactor


class PrivacyAgent:
    """Replaces personal data with placeholders and stores the encrypted mapping (FR-5).
    Runs before every other agent: nothing downstream reads the original."""

    name = "privacy"

    def __init__(self, *, redactor: Redactor, cipher: Cipher) -> None:
        self._redactor = redactor
        self._cipher = cipher

    async def run(self, ctx: EmailContext) -> AgentResult:
        already_done = await ctx.session.scalar(
            select(RedactionRecord.id).where(RedactionRecord.email_id == ctx.email_id)
        )
        if already_done is not None:  # the event was delivered twice
            return AgentResult()

        email = await ctx.session.get_one(Email, ctx.email_id)
        original = OriginalEmail.decrypt(self._cipher, email.original_encrypted)

        # Presidio is synchronous and CPU-bound; a thread keeps the event loop free.
        result = await asyncio.to_thread(self._redactor.redact, [original.subject, original.body])

        email.subject_redacted, email.body_redacted = result.texts
        ctx.session.add_all(
            PiiMapping(
                email_id=email.id,
                placeholder=entry.placeholder,
                encrypted_value=self._cipher.encrypt(entry.value),
                entity_type=entry.entity_type,
            )
            for entry in result.mapping
        )
        ctx.session.add(
            RedactionRecord(
                email_id=email.id,
                entity_counts=dict(Counter(entry.entity_type.value for entry in result.mapping)),
            )
        )
        await ctx.session.flush()
        return AgentResult()
