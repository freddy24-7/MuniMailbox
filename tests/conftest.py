import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Set before anything imports app.main, which builds the app (and its settings) at import
# time. CI points TEST_DATABASE_URL at PostgreSQL; locally each test gets its own SQLite
# file and needs no running services.
# WARNING: every table in TEST_DATABASE_URL is dropped. Never point it at a database you need.
_TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
os.environ["DATABASE_URL"] = _TEST_DATABASE_URL or "sqlite+aiosqlite:///:memory:"
os.environ["ENVIRONMENT"] = "test"
os.environ["ENCRYPTION_KEY"] = Fernet.generate_key().decode()
os.environ["SESSION_SECRET"] = Fernet.generate_key().decode()
os.environ["SPACY_MODEL"] = "nl_core_news_lg"
os.environ["LOCAL_LLM_BASE_URL"] = "http://llm.invalid/v1"  # never called: tests use FakeLLM
os.environ["LOCAL_LLM_MODEL"] = "not-a-real-model"

from fastapi import FastAPI  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db import models  # noqa: E402, F401
from app.db.base import Base  # noqa: E402
from app.db.models import Role, User  # noqa: E402
from app.main import create_app  # noqa: E402
from app.privacy.crypto import Cipher  # noqa: E402
from app.privacy.engine import build_analyzer  # noqa: E402
from app.privacy.redaction import Redactor  # noqa: E402
from tests.helpers import FakeLLM  # noqa: E402


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    return _TEST_DATABASE_URL or f"sqlite+aiosqlite:///{tmp_path / 'test.sqlite3'}"


@pytest.fixture
def settings(database_url: str, tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        database_url=database_url,
        feed_path=tmp_path / "feed.jsonl",
        # SQLite allows one writer at a time; PostgreSQL runs the real concurrency.
        pipeline_workers=1 if database_url.startswith("sqlite") else 5,
    )


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


@pytest.fixture
def cipher(settings: Settings) -> Cipher:
    return Cipher(settings.encryption_key.get_secret_value())


@pytest.fixture(scope="session")
def redactor() -> Redactor:
    """The real Presidio pipeline; loading the spaCy model once per run keeps tests fast."""
    return Redactor(build_analyzer(os.environ["SPACY_MODEL"]), score_threshold=0.4)


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
async def app(
    settings: Settings, engine: AsyncEngine, fake_llm: FakeLLM, redactor: Redactor
) -> AsyncIterator[FastAPI]:
    """The real app with its pipeline running; only the model is fake."""
    app = create_app(settings, local_llm=fake_llm, redactor=redactor)
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def users(session_factory: async_sessionmaker[AsyncSession]) -> dict[Role, User]:
    people = {
        Role.CASEWORKER: User(name="Test Caseworker", role=Role.CASEWORKER, team="Team A"),
        Role.TEAM_LEAD: User(name="Test Team Lead", role=Role.TEAM_LEAD, team="Team A"),
        Role.AUDITOR: User(name="Test Auditor", role=Role.AUDITOR, team=None),
    }
    async with session_factory() as session:
        session.add_all(people.values())
        await session.commit()
    return people
