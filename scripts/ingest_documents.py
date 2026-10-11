"""Chunk, embed and store the fetched documents in ChromaDB (FR-15).

    uv run python scripts/ingest_documents.py data/documents/

Restart the app afterwards: it builds its keyword index from the store at first use.
"""

import argparse
import asyncio
from pathlib import Path

from app.config import get_settings
from app.rag.ingestion import ingest, load_documents
from app.rag.store import ChromaVectorStore
from app.services import build_router


async def _main(directory: Path) -> None:
    settings = get_settings()
    documents = load_documents(directory)
    router, clients = build_router(settings)
    store = ChromaVectorStore(
        host=settings.chroma_host, port=settings.chroma_port, collection=settings.chroma_collection
    )
    try:
        # Public documents only, hence the route that takes no email.
        chunks = await ingest(
            documents, store, router.embed_public, max_chars=settings.chunk_max_chars
        )
    finally:
        for client in clients:
            await client.aclose()
    print(f"Ingested {len(documents)} documents as {chunks} chunks")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("directory", type=Path, nargs="?", default=Path("data/documents"))
    asyncio.run(_main(parser.parse_args().directory))
