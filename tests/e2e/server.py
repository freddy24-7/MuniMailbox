"""The real app in its own process, for the browser tests.

Real HTTP server, real database (a throwaway SQLite file), real redaction and pipeline.
Only the models and the vector store are fakes, so the tests need no Ollama, no Docker
and no network.

    python -m tests.e2e.server <port> <working directory>
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from cryptography.fernet import Fernet


def _configure(workdir: Path) -> None:
    """Environment for `Settings`. Set before the app is imported, which reads it."""
    os.environ.update(
        ENVIRONMENT="test",
        DATABASE_URL=f"sqlite+aiosqlite:///{workdir / 'e2e.sqlite3'}",
        ENCRYPTION_KEY=Fernet.generate_key().decode(),
        SESSION_SECRET=Fernet.generate_key().decode(),
        SPACY_MODEL="nl_core_news_lg",
        LOCAL_LLM_BASE_URL="http://llm.invalid/v1",  # never called: FakeLLM answers
        LOCAL_LLM_MODEL="not-a-real-model",
        EMBEDDING_MODEL="not-a-real-embedding-model",
        EVENT_BUS="memory",
        FEED_PATH=str(workdir / "feed.jsonl"),
        LOG_FORMAT="json",
        LOG_FILE=os.environ.get("E2E_LOG_FILE", str(workdir / "app.log")),
        # SQLite allows one writer at a time.
        PIPELINE_WORKERS="1",
        PARALLEL_AGENTS="false",
    )


async def _prepare(workdir: Path) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db.base import Base
    from app.db.models import Role, User
    from tests.e2e.data import TEAM, USERS
    from tests.helpers import incoming_email

    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine)() as session:
        session.add_all(
            User(name=name, role=Role(role), team=TEAM if role != "auditor" else None)
            for role, name in USERS.items()
        )
        await session.commit()
    await engine.dispose()

    emails = [incoming_email(f"Bericht {number}.") for number in range(3)]
    (workdir / "feed.jsonl").write_text(
        "\n".join(json.dumps(email.model_dump(mode="json")) for email in emails) + "\n",
        encoding="utf-8",
    )


async def _knowledge_base():  # type: ignore[no-untyped-def]
    from app.rag.chunking import Document
    from app.rag.ingestion import ingest
    from app.rag.store import InMemoryVectorStore
    from tests.e2e.data import SOURCE_TITLE, SOURCE_URL
    from tests.helpers import FakeEmbedder

    store, embedder = InMemoryVectorStore(), FakeEmbedder()
    document = Document(
        url=SOURCE_URL,
        title=SOURCE_TITLE,
        topic="waste_collection",
        fetched_at="2026-10-01",
        text=f"# {SOURCE_TITLE}\nGrofvuil laten ophalen kost € 25.",
    )
    await ingest([document], store, embedder.embed, max_chars=1200)
    return store, embedder


def main() -> None:
    port, workdir = int(sys.argv[1]), Path(sys.argv[2])
    _configure(workdir)
    asyncio.run(_prepare(workdir))

    import uvicorn

    from app.config import Settings
    from app.main import create_app
    from tests.e2e.data import DRAFT_REPLY
    from tests.helpers import FakeLLM

    store, embedder = asyncio.run(_knowledge_base())
    llm = FakeLLM()
    llm.reply, llm.sources_used = DRAFT_REPLY, [1]
    app = create_app(Settings(_env_file=None), local_llm=llm, embedder=embedder, vector_store=store)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
