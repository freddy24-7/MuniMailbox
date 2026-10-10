import time
from collections.abc import Sequence

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agents.base import Agent, EmailContext
from app.audit import writer as audit
from app.db.models import Email, EmailStatus
from app.db.status import ALLOWED_TRANSITIONS, transition
from app.events.bus import Event, EventBus
from app.inference.router import UnredactedInputError

logger = structlog.get_logger(__name__)

EMAIL_RECEIVED = "email.received"


def completed(agent: Agent) -> str:
    return f"{agent.name}.completed"


class Orchestrator:
    """Runs the agents in order, one event at a time (FR-2).

    Each agent is triggered by the completion event of the one before it. A step is one
    database transaction: its results, the status change and the audit event are saved
    together, and only then is the next event published.
    """

    def __init__(
        self,
        *,
        bus: EventBus,
        session_factory: async_sessionmaker[AsyncSession],
        agents: Sequence[Agent],
    ) -> None:
        self._bus = bus
        self._session_factory = session_factory
        self._agents = list(agents)
        triggers = [EMAIL_RECEIVED, *(completed(agent) for agent in agents[:-1])]
        self._step_for = dict(zip(triggers, self._agents, strict=True))

    def register(self) -> None:
        for trigger in self._step_for:
            self._bus.subscribe(trigger, self._handle)

    async def _handle(self, event: Event) -> None:
        agent = self._step_for[event.name]
        started = time.perf_counter()
        try:
            async with self._session_factory() as session:
                email = await session.get_one(Email, event.email_id)
                if email.status not in (EmailStatus.RECEIVED, EmailStatus.PROCESSING):
                    return  # duplicate or late event for an email that has moved on
                if email.status is EmailStatus.RECEIVED:
                    transition(email, EmailStatus.PROCESSING)

                result = await agent.run(EmailContext(email.id, email.correlation_id, session))

                if agent is self._agents[-1]:
                    transition(email, EmailStatus.DRAFT_READY)
                await audit.record(
                    session,
                    actor=f"agent:{agent.name}",
                    action=completed(agent),
                    email_id=email.id,
                    model=result.model,
                    left_local=result.left_local,
                )
                await session.commit()
        except Exception as error:
            await self._fail(event, agent, error)
            return

        logger.info(
            "agent.completed",
            agent=agent.name,
            duration_ms=round((time.perf_counter() - started) * 1000),
        )
        await self._bus.publish(Event(completed(agent), event.email_id, event.correlation_id))

    async def _fail(self, event: Event, agent: Agent, error: Exception) -> None:
        """Retries arrive with the full agent set (FR-3); until then a failed step goes
        straight to a human."""
        logger.exception("agent.failed", agent=agent.name, error_type=type(error).__name__)
        refused = isinstance(error, UnredactedInputError)
        async with self._session_factory() as session:
            email = await session.get_one(Email, event.email_id)
            if EmailStatus.NEEDS_ATTENTION in ALLOWED_TRANSITIONS[email.status]:
                transition(email, EmailStatus.NEEDS_ATTENTION)
            await audit.record(
                session,
                actor=f"agent:{agent.name}",
                action="inference.refused" if refused else f"{agent.name}.failed",
                email_id=email.id,
            )
            await session.commit()
