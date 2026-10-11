import re
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import select

from app.agents.base import AgentResult, EmailContext
from app.agents.drafting import format_sources
from app.audit import writer as audit
from app.db.models import Draft, Email, PiiMapping
from app.inference.router import InferenceRouter, UnredactedInputError
from app.privacy.redaction import RedactedText

PROMPT_NAME = "quality.v1"
MAX_MODEL_WARNINGS = 5
NOT_CHECKED: dict[str, Any] = {
    "type": "not_checked",
    "message": "The automatic check of tone, completeness and unsupported claims failed. "
    "Only the rule-based checks ran.",
    "origin": "rule",
}

_PLACEHOLDER = re.compile(r"\[[A-Z_]+_\d+\]")
# Amounts, durations and short phone numbers: the facts a wrong reply gets wrong.
_FACT = re.compile(
    r"€\s?\d+(?:[.,]\d+)*|\b\d+(?:[.,]\d+)?\s?(?:euro|werkdagen|dagen|weken|maanden|uur|jaar)\b|\b14\s?0\d{2,3}\b",
    re.IGNORECASE,
)


class Finding(BaseModel):
    type: Literal["tone", "completeness", "unsupported_claim", "contradiction"]
    message: str


class QualityReport(BaseModel):
    warnings: list[Finding] = []


def _normalise(fact: str) -> str:
    return re.sub(r"\s+", "", fact.lower())


def rule_based_warnings(
    draft: Draft, email: Email, known_placeholders: set[str]
) -> list[dict[str, Any]]:
    """Checks that need no model and cannot be talked out of their answer."""
    warnings: list[dict[str, Any]] = []
    policy_context = email.policy_context or []

    if not draft.sources:
        message = (
            "The model cited none of the documents it was given."
            if policy_context
            else "No relevant document was found; this reply is not based on the knowledge base."
        )
        warnings.append({"type": "no_source", "message": message})

    for placeholder in sorted(set(_PLACEHOLDER.findall(draft.text_redacted)) - known_placeholders):
        warnings.append(
            {
                "type": "unknown_placeholder",
                "message": f"{placeholder} is not in this email's data and will be shown as is.",
            }
        )

    evidence = _normalise(
        " ".join(source["text"] for source in policy_context) + " " + (email.body_redacted or "")
    )
    for fact in dict.fromkeys(match.group() for match in _FACT.finditer(draft.text_redacted)):
        if _normalise(fact) not in evidence:
            warnings.append(
                {
                    "type": "unsupported_number",
                    "message": f'"{fact}" appears in the reply but in none of the sources.',
                }
            )
    return [{**warning, "origin": "rule"} for warning in warnings]


class QualityAgent:
    """Checks the draft before a human sees it and attaches warnings (FR-11).

    Two kinds of check: rules, which are exact but narrow, and a second model call, which
    is broad but can itself be wrong. Warnings inform the caseworker; they block nothing.

    The model check is advisory, so its failure does not cost the caseworker the draft:
    when the last attempt fails, the rule checks still run and the draft is marked as not
    checked by a model. A refusal by the router is never treated that way.
    """

    name = "quality"

    def __init__(self, *, router: InferenceRouter) -> None:
        self._router = router

    async def run(self, ctx: EmailContext) -> AgentResult:
        email = await ctx.session.get_one(Email, ctx.email_id)
        draft = (
            await ctx.session.scalars(
                select(Draft)
                .where(Draft.email_id == email.id)
                .order_by(Draft.created_at.desc())
                .limit(1)
            )
        ).one()
        placeholders = set(
            await ctx.session.scalars(
                select(PiiMapping.placeholder).where(PiiMapping.email_id == email.id)
            )
        )

        outcome = AgentResult()
        try:
            result = await self._router.complete_json(
                ctx.session,
                email_id=email.id,
                actor=f"agent:{self.name}",
                prompt_name=PROMPT_NAME,
                variables={
                    "subject": RedactedText(email.subject_redacted or ""),
                    "body": RedactedText(email.body_redacted or ""),
                    # Written by a model that only saw redacted text.
                    "draft": RedactedText(draft.text_redacted),
                },
                public_context={"sources": format_sources(email.policy_context or [])},
                schema=QualityReport,
            )
        except UnredactedInputError:
            raise
        except Exception:
            if not ctx.final_attempt:
                raise  # the orchestrator retries
            await audit.record(
                ctx.session,
                actor=f"agent:{self.name}",
                action="quality.model_check_failed",
                email_id=email.id,
            )
            model_warnings = [NOT_CHECKED]
        else:
            outcome = AgentResult(model=result.model, left_local=result.left_local)
            model_warnings = [
                {"type": finding.type, "message": finding.message, "origin": "model"}
                for finding in result.output.warnings[:MAX_MODEL_WARNINGS]
            ]
        # Reassigned, not appended to: SQLAlchemy does not notice changes inside a JSON list.
        draft.warnings = [
            *draft.warnings,
            *rule_based_warnings(draft, email, placeholders),
            *model_warnings,
        ]
        await ctx.session.flush()
        return outcome
