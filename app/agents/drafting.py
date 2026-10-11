from typing import Any

from pydantic import BaseModel, Field

from app.agents.base import AgentResult, EmailContext
from app.db.models import Draft, Email
from app.inference.router import InferenceRouter
from app.privacy.redaction import RedactedText
from app.rag.chunking import PublicText

PROMPT_NAME = "draft_reply.v2"
NO_SOURCES = "(geen bronnen gevonden)"
NO_CASES = "(geen eerdere zaken bekend)"
# What is kept of a cited source: enough to show and link it, not its text.
SOURCE_FIELDS = ("id", "title", "heading", "url", "fetched_at")


class DraftOutput(BaseModel):
    reply: str = Field(min_length=1)
    sources_used: list[int] = []


def format_sources(policy_context: list[dict[str, Any]]) -> PublicText:
    """The numbered passages as the model sees them. No URLs: the model cites a number,
    and the code looks the link up, so a link can never be made up."""
    if not policy_context:
        return PublicText(NO_SOURCES)
    return PublicText(
        "\n\n".join(f"[{source['id']}] {source['text']}" for source in policy_context)
    )


def format_cases(case_context: list[dict[str, Any]]) -> RedactedText:
    if not case_context:
        return RedactedText(NO_CASES)
    return RedactedText(
        "\n".join(
            f"- {case['opened']}, {case['status']}: {case['summary_redacted']}"
            for case in case_context
        )
    )


class DraftingAgent:
    """Writes the draft reply in one model call from the redacted email, the retrieved
    sources and the case history (FR-10), and records which sources it used (FR-17)."""

    name = "drafting"

    def __init__(self, *, router: InferenceRouter) -> None:
        self._router = router

    async def run(self, ctx: EmailContext) -> AgentResult:
        email = await ctx.session.get_one(Email, ctx.email_id)
        policy_context = email.policy_context or []
        result = await self._router.complete_json(
            ctx.session,
            email_id=email.id,
            actor=f"agent:{self.name}",
            prompt_name=PROMPT_NAME,
            variables={
                # Columns named *_redacted hold placeholders only; "" if privacy never ran,
                # in which case the router refuses for lack of a redaction record.
                "subject": RedactedText(email.subject_redacted or ""),
                "body": RedactedText(email.body_redacted or ""),
                "case_history": format_cases(email.case_context or []),
            },
            public_context={"sources": format_sources(policy_context)},
            schema=DraftOutput,
        )

        # Never let the model invent a source: a number that was not offered is dropped
        # and reported, and titles and links come from our own records.
        offered = {source["id"]: source for source in policy_context}
        cited = [
            number for number in dict.fromkeys(result.output.sources_used) if number in offered
        ]
        invented = sorted(set(result.output.sources_used) - set(offered))
        warnings = [
            {
                "type": "invalid_citation",
                "message": f"The model cited source {number}, which it was not given.",
                "origin": "rule",
            }
            for number in invented
        ]
        ctx.session.add(
            Draft(
                email_id=email.id,
                text_redacted=result.output.reply.strip(),
                sources=[
                    {
                        key: offered[number][key]
                        for key in ("id", "title", "heading", "url", "fetched_at")
                    }
                    for number in cited
                ],
                warnings=warnings,
                model_used=result.model,
            )
        )
        await ctx.session.flush()
        return AgentResult(model=result.model, left_local=result.left_local)
