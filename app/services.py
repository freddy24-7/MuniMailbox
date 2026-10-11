import asyncio
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.agents.case import CaseAgent
from app.agents.drafting import DraftingAgent
from app.agents.intake import IntakeAgent
from app.agents.policy import PolicyAgent
from app.agents.privacy import PrivacyAgent
from app.agents.quality import QualityAgent
from app.auth.session import SessionCodec
from app.config import Settings
from app.db.session import create_engine, create_session_factory
from app.events.bus import InMemoryEventBus
from app.inference.clients import ChatClient, EmbeddingClient, OpenAICompatibleClient
from app.inference.router import InferenceRouter
from app.orchestrator.workflow import Orchestrator
from app.privacy.crypto import Cipher
from app.privacy.engine import build_analyzer
from app.privacy.redaction import Redactor
from app.rag.retrieval import Retriever
from app.rag.store import ChromaVectorStore, VectorStore


@dataclass(frozen=True)
class Services:
    """Everything long-lived, built once at startup and shared by all requests."""

    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    cipher: Cipher
    session_codec: SessionCodec
    bus: InMemoryEventBus
    http_clients: list[OpenAICompatibleClient]

    async def aclose(self) -> None:
        await self.bus.stop()
        for client in self.http_clients:
            await client.aclose()
        await self.engine.dispose()


def build_router(
    settings: Settings,
    *,
    local_llm: ChatClient | None = None,
    cloud_llm: ChatClient | None = None,
    embedder: EmbeddingClient | None = None,
) -> tuple[InferenceRouter, list[OpenAICompatibleClient]]:
    """The router and the HTTP clients it owns (to be closed at shutdown). Tests pass
    fakes; anything not passed is built from the settings."""
    owned: list[OpenAICompatibleClient] = []

    def client(
        base_url: str, model: str, timeout: float, key: str | None = None
    ) -> OpenAICompatibleClient:
        owned.append(
            OpenAICompatibleClient(
                base_url=base_url, model=model, timeout_seconds=timeout, api_key=key
            )
        )
        return owned[-1]

    local_url, local_timeout = settings.local_llm_base_url, settings.local_llm_timeout_seconds
    if local_llm is None:
        local_llm = client(local_url, settings.local_llm_model, local_timeout)
    if embedder is None:
        embedder = client(local_url, settings.embedding_model, local_timeout)
    if cloud_llm is None and settings.cloud_llm_enabled:
        if not (settings.cloud_llm_base_url and settings.cloud_llm_model):
            raise ValueError("CLOUD_LLM_ENABLED needs CLOUD_LLM_BASE_URL and CLOUD_LLM_MODEL")
        key = settings.cloud_llm_api_key
        cloud_llm = client(
            settings.cloud_llm_base_url,
            settings.cloud_llm_model,
            settings.cloud_llm_timeout_seconds,
            key.get_secret_value() if key else None,
        )
    router = InferenceRouter(
        local=local_llm,
        cloud=cloud_llm,
        cloud_route_min_chars=settings.cloud_route_min_chars,
        embedder=embedder,
        local_max_concurrency=settings.local_llm_max_concurrency,
        cipher=Cipher(settings.encryption_key.get_secret_value()),
    )
    return router, owned


async def build_services(
    settings: Settings,
    *,
    local_llm: ChatClient | None = None,
    cloud_llm: ChatClient | None = None,
    embedder: EmbeddingClient | None = None,
    redactor: Redactor | None = None,
    vector_store: VectorStore | None = None,
) -> Services:
    """Tests supply fakes for the models and the vector store, and one shared redactor so
    the spaCy model is loaded once per test run."""
    engine = create_engine(settings.database_url.get_secret_value())
    session_factory = create_session_factory(engine)
    cipher = Cipher(settings.encryption_key.get_secret_value())
    if redactor is None:
        analyzer = await asyncio.to_thread(build_analyzer, settings.spacy_model)
        redactor = Redactor(analyzer, settings.redaction_score_threshold)
    if vector_store is None:
        vector_store = ChromaVectorStore(
            host=settings.chroma_host,
            port=settings.chroma_port,
            collection=settings.chroma_collection,
        )
    router, http_clients = build_router(
        settings, local_llm=local_llm, cloud_llm=cloud_llm, embedder=embedder
    )
    retriever = Retriever(
        vector_store,
        top_k=settings.retrieval_top_k,
        min_similarity=settings.retrieval_min_similarity,
    )

    bus = InMemoryEventBus(workers=settings.pipeline_workers)
    Orchestrator(
        bus=bus,
        session_factory=session_factory,
        # The order is a safety property: privacy first, and nothing reads the original after it.
        steps=[
            PrivacyAgent(redactor=redactor, cipher=cipher),
            IntakeAgent(router=router, team_by_topic=settings.team_by_topic),
            # Neither needs the other's result, so they run at the same time (FR-9).
            (
                PolicyAgent(router=router, retriever=retriever),
                CaseAgent(redactor=redactor, cipher=cipher),
            ),
            DraftingAgent(router=router),
            QualityAgent(router=router),
        ],
        max_retries=settings.max_step_retries,
        backoff_seconds=settings.retry_backoff_seconds,
        parallel=settings.parallel_agents,
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
        http_clients=http_clients,
    )
