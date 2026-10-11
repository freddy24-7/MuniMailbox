import asyncio
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

# A step is one agent, or several that do not depend on each other and run together.
Step = Agent | tuple[Agent, ...]


def completed(agent: Agent) -> str:
    return f"{agent.name}.completed"


def _agents(step: Step) -> tuple[Agent, ...]:
    return step if isinstance(step, tuple) else (step,)


def _step_completed(step: Step) -> str:
    return "+".join(agent.name for agent in _agents(step)) + ".completed"


class Orchestrator:
    """Runs the workflow, one step per event (FR-2).

    Each step is triggered by the completion event of the step before it. An agent runs
    in one database transaction: its results, the status change and the audit event are
    saved together, and only then is the next event published.

    A step with several agents runs them at the same time (FR-9) and continues when all
    are done. A failing agent is retried; when the retries are used up, the email goes to
    a human (FR-3).
    """

    def __init__(
        self,
        *,
        bus: EventBus,
        session_factory: async_sessionmaker[AsyncSession],
        steps: Sequence[Step],
        max_retries: int = 0,
        backoff_seconds: float = 0.0,
        parallel: bool = True,
    ) -> None:
        self._bus = bus
        self._session_factory = session_factory
        self._steps = list(steps)
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        self._parallel = parallel
        triggers = [EMAIL_RECEIVED, *(_step_completed(step) for step in steps[:-1])]
        self._step_for = dict(zip(triggers, self._steps, strict=True))

    def register(self) -> None:
        for trigger in self._step_for:
            self._bus.subscribe(trigger, self._handle)

    async def _handle(self, event: Event) -> None:
        step = self._step_for[event.name]
        agents = _agents(step)
        final = step is self._steps[-1]

        if self._parallel and len(agents) > 1:
            # Fan-out / fan-in: start all, wait for all. Each has its own transaction.
            outcomes = await asyncio.gather(
                *(self._run_with_retries(event, agent, final) for agent in agents)
            )
        else:
            outcomes = [await self._run_with_retries(event, agent, final) for agent in agents]

        if all(outcomes):
            if len(agents) > 1:
                for agent in agents:
                    await self._bus.publish(
                        Event(completed(agent), event.email_id, event.correlation_id)
                    )
            await self._bus.publish(
                Event(_step_completed(step), event.email_id, event.correlation_id)
            )

    async def _run_with_retries(self, event: Event, agent: Agent, final: bool) -> bool:
        for attempt in range(self._max_retries + 1):
            started = time.perf_counter()
            try:
                ran = await self._run_once(
                    event, agent, final, final_attempt=attempt == self._max_retries
                )
            except UnredactedInputError as error:
                # Deterministic: the same input would be refused again.
                await self._fail(event, agent, error, action="inference.refused")
                return False
            except Exception as error:
                if attempt == self._max_retries:
                    await self._fail(event, agent, error, action=f"{agent.name}.failed")
                    return False
                logger.warning(
                    "agent.retrying",
                    agent=agent.name,
                    attempt=attempt + 1,
                    error_type=type(error).__name__,
                )
                await self._audit(event, agent, f"{agent.name}.retried")
                # Wait longer each time: a struggling model server needs room, not pressure.
                await asyncio.sleep(self._backoff_seconds * 2**attempt)
                continue
            if ran:
                logger.info(
                    "agent.completed",
                    agent=agent.name,
                    duration_ms=round((time.perf_counter() - started) * 1000),
                )
            return ran
        return False

    async def _run_once(
        self, event: Event, agent: Agent, final: bool, *, final_attempt: bool
    ) -> bool:
        async with self._session_factory() as session:
            email = await session.get_one(Email, event.email_id)
            if email.status not in (EmailStatus.RECEIVED, EmailStatus.PROCESSING):
                return False  # duplicate or late event for an email that has moved on
            if email.status is EmailStatus.RECEIVED:
                transition(email, EmailStatus.PROCESSING)

            result = await agent.run(
                EmailContext(email.id, email.correlation_id, session, final_attempt)
            )

            if final:
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
        return True

    async def _audit(self, event: Event, agent: Agent, action: str) -> None:
        async with self._session_factory() as session:
            await audit.record(
                session, actor=f"agent:{agent.name}", action=action, email_id=event.email_id
            )
            await session.commit()

    async def _fail(self, event: Event, agent: Agent, error: Exception, *, action: str) -> None:
        logger.exception("agent.failed", agent=agent.name, error_type=type(error).__name__)
        async with self._session_factory() as session:
            email = await session.get_one(Email, event.email_id)
            if EmailStatus.NEEDS_ATTENTION in ALLOWED_TRANSITIONS[email.status]:
                transition(email, EmailStatus.NEEDS_ATTENTION)
            await audit.record(
                session, actor=f"agent:{agent.name}", action=action, email_id=email.id
            )
            await session.commit()
