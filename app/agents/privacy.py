import asyncio
from collections import Counter

from sqlalchemy import select

from app.agents.base import AgentResult, EmailContext
from app.db.models import Citizen, Email, PiiMapping, RedactionRecord
from app.observability import metrics
from app.privacy.crypto import Cipher
from app.privacy.hints import known_values_for
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
        if email.original_encrypted is None:
            raise RuntimeError("original was deleted by retention before it was redacted")
        original = OriginalEmail.decrypt(self._cipher, email.original_encrypted)

        citizen = await ctx.session.get(Citizen, email.citizen_id) if email.citizen_id else None
        known_values = known_values_for(citizen) if citizen else []

        # Presidio is synchronous and CPU-bound; a thread keeps the event loop free.
        result = await asyncio.to_thread(
            self._redactor.redact, [original.subject, original.body], known_values
        )

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
        # Counts per type only: which values were replaced is never a metric.
        metrics.REDACTIONS_PER_EMAIL.observe(len(result.mapping))
        for entry in result.mapping:
            metrics.REDACTIONS.labels(entity_type=entry.entity_type.value).inc()
        return AgentResult()
