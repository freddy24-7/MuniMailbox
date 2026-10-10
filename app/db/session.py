from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(database_url: str) -> AsyncEngine:
    # hide_parameters: SQL errors are logged, and their parameters can be personal data.
    return create_async_engine(database_url, pool_pre_ping=True, hide_parameters=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, never shared between requests."""
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.services.session_factory
    async with session_factory() as session:
        yield session
