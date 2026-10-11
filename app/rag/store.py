from dataclasses import asdict
from typing import Any, Protocol

import chromadb

from app.rag.chunking import Chunk

Embedding = list[float]


class VectorStore(Protocol):
    """Where chunks and their embeddings live. ChromaDB in the running app; an in-memory
    implementation in tests, behind the same interface (as with the event bus)."""

    async def replace_url(
        self, url: str, chunks: list[Chunk], embeddings: list[list[float]]
    ) -> None:
        """Store a page's chunks, removing whatever was stored for that URL before."""
        ...

    async def nearest(
        self, embedding: list[float], *, limit: int, topic: str | None
    ) -> list[tuple[Chunk, float]]:
        """Chunks closest in meaning, with cosine similarity (1.0 = same direction)."""
        ...

    async def all_chunks(self) -> list[Chunk]: ...


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = (sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5)
    return dot / norm if norm else 0.0


class InMemoryVectorStore:
    def __init__(self) -> None:
        self._items: dict[str, tuple[Chunk, list[float]]] = {}

    async def replace_url(
        self, url: str, chunks: list[Chunk], embeddings: list[list[float]]
    ) -> None:
        self._items = {k: v for k, v in self._items.items() if v[0].url != url}
        for chunk, embedding in zip(chunks, embeddings, strict=True):
            self._items[chunk.id] = (chunk, embedding)

    async def nearest(
        self, embedding: list[float], *, limit: int, topic: str | None
    ) -> list[tuple[Chunk, float]]:
        scored = [
            (chunk, _cosine(embedding, stored))
            for chunk, stored in self._items.values()
            if topic is None or chunk.topic == topic
        ]
        return sorted(scored, key=lambda item: -item[1])[:limit]

    async def all_chunks(self) -> list[Chunk]:
        return [chunk for chunk, _ in self._items.values()]


def _to_chunk(chunk_id: str, document: str, metadata: Any) -> Chunk:
    return Chunk(
        id=chunk_id,
        text=document,
        title=str(metadata["title"]),
        heading=str(metadata["heading"]),
        url=str(metadata["url"]),
        topic=str(metadata["topic"]),
        fetched_at=str(metadata["fetched_at"]),
    )


class ChromaVectorStore:
    def __init__(self, *, host: str, port: int, collection: str) -> None:
        self._host, self._port, self._name = host, port, collection
        self._collection: Any = None

    async def _get(self) -> Any:
        if self._collection is None:
            client = await chromadb.AsyncHttpClient(host=self._host, port=self._port)
            self._collection = await client.get_or_create_collection(
                self._name, configuration={"hnsw": {"space": "cosine"}}
            )
        return self._collection

    async def replace_url(
        self, url: str, chunks: list[Chunk], embeddings: list[list[float]]
    ) -> None:
        collection = await self._get()
        await collection.delete(where={"url": url})
        if not chunks:
            return
        metadatas = []
        for chunk in chunks:
            metadata = asdict(chunk)
            del metadata["id"], metadata["text"]
            metadatas.append(metadata)
        await collection.add(
            ids=[chunk.id for chunk in chunks],
            embeddings=embeddings,
            documents=[chunk.text for chunk in chunks],
            metadatas=metadatas,
        )

    async def nearest(
        self, embedding: list[float], *, limit: int, topic: str | None
    ) -> list[tuple[Chunk, float]]:
        collection = await self._get()
        result = await collection.query(
            query_embeddings=[embedding],
            n_results=limit,
            where={"topic": topic} if topic else None,
            include=["documents", "metadatas", "distances"],
        )
        return [
            # Chroma returns cosine *distance*; similarity is its complement.
            (_to_chunk(chunk_id, document, metadata), 1.0 - distance)
            for chunk_id, document, metadata, distance in zip(
                result["ids"][0],
                result["documents"][0],
                result["metadatas"][0],
                result["distances"][0],
                strict=True,
            )
        ]

    async def all_chunks(self) -> list[Chunk]:
        collection = await self._get()
        result = await collection.get(include=["documents", "metadatas"])
        return [
            _to_chunk(chunk_id, document, metadata)
            for chunk_id, document, metadata in zip(
                result["ids"], result["documents"], result["metadatas"], strict=True
            )
        ]
