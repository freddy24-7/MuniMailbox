from app.agents.base import AgentResult, EmailContext
from app.db.models import Draft, Email
from app.inference.router import InferenceRouter
from app.privacy.redaction import RedactedText

PROMPT_NAME = "draft_reply.v1"


class DraftingAgent:
    """Writes a Dutch draft reply from the redacted email (FR-10). Sources and case
    context join the prompt once the policy and case agents exist."""

    name = "drafting"

    def __init__(self, *, router: InferenceRouter) -> None:
        self._router = router

    async def run(self, ctx: EmailContext) -> AgentResult:
        email = await ctx.session.get_one(Email, ctx.email_id)
        result = await self._router.complete(
            ctx.session,
            email_id=email.id,
            actor=f"agent:{self.name}",
            prompt_name=PROMPT_NAME,
            variables={
                # Columns named *_redacted hold placeholders only; "" if privacy never ran,
                # in which case the router refuses for lack of a redaction record.
                "subject": RedactedText(email.subject_redacted or ""),
                "body": RedactedText(email.body_redacted or ""),
            },
        )
        ctx.session.add(
            Draft(
                email_id=email.id,
                text_redacted=result.text,
                sources=[],
                warnings=[],
                model_used=result.model,
            )
        )
        await ctx.session.flush()
        return AgentResult(model=result.model, left_local=result.left_local)
