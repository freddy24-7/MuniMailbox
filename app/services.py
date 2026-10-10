import asyncio
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.agents.drafting import DraftingAgent
from app.agents.privacy import PrivacyAgent
from app.auth.session import SessionCodec
from app.config import Settings
from app.db.session import create_engine, create_session_factory
from app.events.bus import InMemoryEventBus
from app.inference.clients import ChatClient, OpenAICompatibleClient
from app.inference.router import InferenceRouter
from app.orchestrator.workflow import Orchestrator
from app.privacy.crypto import Cipher
from app.privacy.engine import build_analyzer
from app.privacy.redaction import Redactor


@dataclass(frozen=True)
class Services:
    """Everything long-lived, built once at startup and shared by all requests."""

    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    cipher: Cipher
    session_codec: SessionCodec
    bus: InMemoryEventBus
    local_llm: ChatClient

    async def aclose(self) -> None:
        await self.bus.stop()
        if isinstance(self.local_llm, OpenAICompatibleClient):
            await self.local_llm.aclose()
        await self.engine.dispose()


async def build_services(
    settings: Settings, *, local_llm: ChatClient | None = None, redactor: Redactor | None = None
) -> Services:
    """`local_llm` and `redactor` can be supplied by tests: a fake model, and one shared
    redactor so the spaCy model is loaded once per test run."""
    engine = create_engine(settings.database_url.get_secret_value())
    session_factory = create_session_factory(engine)
    cipher = Cipher(settings.encryption_key.get_secret_value())
    if redactor is None:
        analyzer = await asyncio.to_thread(build_analyzer, settings.spacy_model)
        redactor = Redactor(analyzer, settings.redaction_score_threshold)
    if local_llm is None:
        local_llm = OpenAICompatibleClient(
            base_url=settings.local_llm_base_url,
            model=settings.local_llm_model,
            timeout_seconds=settings.local_llm_timeout_seconds,
        )

    bus = InMemoryEventBus(workers=settings.pipeline_workers)
    router = InferenceRouter(local=local_llm, cipher=cipher)
    Orchestrator(
        bus=bus,
        session_factory=session_factory,
        # Order is a safety property: privacy first.
        agents=[PrivacyAgent(redactor=redactor, cipher=cipher), DraftingAgent(router=router)],
    ).register()
    await bus.start()

    return Services(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        cipher=cipher,
        session_codec=SessionCodec(
            settings.session_secret.get_secret_value(), settings.session_ttl_seconds
        ),
        bus=bus,
        local_llm=local_llm,
    )
