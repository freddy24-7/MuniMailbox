import asyncio

from sqlalchemy import select

from app.agents.base import AgentResult, EmailContext
from app.db.models import Case, Citizen, Email, PiiMapping
from app.privacy.crypto import Cipher
from app.privacy.hints import known_values_for
from app.privacy.redaction import MappingEntry, Redactor

MAX_CASES = 5


class CaseAgent:
    """Looks up the sender's earlier cases in the case system (FR-8).

    The sender is identified by `Email.citizen_id`, set at intake from the sender's
    address: the redacted text no longer says who wrote it. Case summaries are free text
    and may name people, so they are redacted with the email's own mapping before they
    are stored for the drafting agent.
    """

    name = "case"

    def __init__(self, *, redactor: Redactor, cipher: Cipher) -> None:
        self._redactor = redactor
        self._cipher = cipher

    async def run(self, ctx: EmailContext) -> AgentResult:
        email = await ctx.session.get_one(Email, ctx.email_id)
        citizen = await ctx.session.get(Citizen, email.citizen_id) if email.citizen_id else None
        if citizen is None:
            email.case_context = []
            await ctx.session.flush()
            return AgentResult()

        cases = (
            await ctx.session.scalars(
                select(Case)
                .where(Case.citizen_id == citizen.id)
                .order_by(Case.opened_at.desc())
                .limit(MAX_CASES)
            )
        ).all()
        rows = (
            await ctx.session.scalars(select(PiiMapping).where(PiiMapping.email_id == email.id))
        ).all()
        existing = [
            MappingEntry(
                row.placeholder, row.entity_type, self._cipher.decrypt(row.encrypted_value)
            )
            for row in rows
        ]
        result = await asyncio.to_thread(
            self._redactor.redact,
            [case.summary for case in cases],
            known_values_for(citizen),
            existing,
        )

        known_placeholders = {entry.placeholder for entry in existing}
        ctx.session.add_all(
            PiiMapping(
                email_id=email.id,
                placeholder=entry.placeholder,
                encrypted_value=self._cipher.encrypt(entry.value),
                entity_type=entry.entity_type,
            )
            for entry in result.mapping
            if entry.placeholder not in known_placeholders
        )
        email.case_context = [
            {
                "topic": case.topic.value,
                "status": case.status.value,
                "opened": case.opened_at.strftime("%Y-%m"),
                "summary_redacted": summary,
            }
            for case, summary in zip(cases, result.texts, strict=True)
        ]
        await ctx.session.flush()
        return AgentResult()
