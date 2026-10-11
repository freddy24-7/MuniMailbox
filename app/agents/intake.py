from collections.abc import Mapping

from pydantic import BaseModel

from app.agents.base import AgentResult, EmailContext
from app.db.models import Email, Topic, Urgency
from app.inference.router import InferenceRouter
from app.privacy.redaction import RedactedText

PROMPT_NAME = "intake.v1"


class Classification(BaseModel):
    """The only answers the model can give: anything else fails validation (FR-6)."""

    topic: Topic
    urgency: Urgency
    sensitive: bool


class IntakeAgent:
    """Classifies the email and routes it to the team that handles its topic."""

    name = "intake"

    def __init__(self, *, router: InferenceRouter, team_by_topic: Mapping[str, str]) -> None:
        self._router = router
        self._team_by_topic = team_by_topic

    async def run(self, ctx: EmailContext) -> AgentResult:
        email = await ctx.session.get_one(Email, ctx.email_id)
        result = await self._router.complete_json(
            ctx.session,
            email_id=email.id,
            actor=f"agent:{self.name}",
            prompt_name=PROMPT_NAME,
            variables={
                "subject": RedactedText(email.subject_redacted or ""),
                "body": RedactedText(email.body_redacted or ""),
            },
            schema=Classification,
        )
        email.topic = result.output.topic
        email.urgency = result.output.urgency
        email.sensitive = result.output.sensitive
        # Topics without a team stay where they are, for a team lead to route.
        email.team = self._team_by_topic.get(email.topic.value, email.team)
        await ctx.session.flush()
        return AgentResult(model=result.model, left_local=result.left_local)
