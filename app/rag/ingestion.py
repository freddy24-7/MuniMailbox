import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path

from app.rag.chunking import Document, chunk_document
from app.rag.store import VectorStore

EmbedDocuments = Callable[[list[str]], Awaitable[list[list[float]]]]


def save_document(document: Document, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    slug = document.url.rstrip("/").rsplit("/", 1)[-1]
    path = directory / f"{document.topic}__{slug}.json"
    path.write_text(json.dumps(asdict(document), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_documents(directory: Path) -> list[Document]:
    return [
        Document(**json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(directory.glob("*.json"))
    ]


async def ingest(
    documents: list[Document],
    store: VectorStore,
    embed: EmbedDocuments,
    *,
    max_chars: int,
    batch_size: int = 16,
) -> int:
    """Chunk, embed and store each document (FR-15). Re-ingesting a URL replaces its old
    chunks, so the script can be run again when pages change. Returns the chunk count."""
    total = 0
    for document in documents:
        chunks = chunk_document(document, max_chars=max_chars)
        embeddings: list[list[float]] = []
        for start in range(0, len(chunks), batch_size):
            embeddings += await embed([chunk.text for chunk in chunks[start : start + batch_size]])
        await store.replace_url(document.url, chunks, embeddings)
        total += len(chunks)
    return total
