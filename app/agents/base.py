import uuid
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class EmailContext:
    email_id: uuid.UUID
    correlation_id: uuid.UUID
    # The orchestrator owns the transaction: agents add and flush, they do not commit.
    session: AsyncSession
    # True when a failure now would not be retried. An agent whose work is advisory can
    # then finish without it instead of failing the email.
    final_attempt: bool = True


@dataclass(frozen=True)
class AgentResult:
    model: str | None = None
    left_local: bool = False


class Agent(Protocol):
    """Agents are stateless: everything they produce goes into the database."""

    name: str

    async def run(self, ctx: EmailContext) -> AgentResult: ...
