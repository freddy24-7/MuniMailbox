from dataclasses import dataclass
from typing import Literal

from app.rag.bm25 import Bm25Index
from app.rag.chunking import Chunk
from app.rag.store import VectorStore

# The usual constant in reciprocal rank fusion; it damps the difference between ranks.
_RRF_K = 60

# "hybrid" is what the app uses. The other two run one method alone, to compare them.
Method = Literal["hybrid", "semantic", "keyword"]


@dataclass(frozen=True)
class RetrievedChunk:
    chunk: Chunk
    score: float
    # Where each method ranked it (1 = best), or None if that method did not return it.
    # Kept so the two methods can be compared later.
    semantic_rank: int | None
    keyword_rank: int | None
    similarity: float | None


class Retriever:
    """Hybrid search (FR-16): by meaning and by exact words, merged by rank."""

    def __init__(self, store: VectorStore, *, top_k: int, min_similarity: float) -> None:
        self._store = store
        self._top_k = top_k
        self._min_similarity = min_similarity
        self._keyword_index: Bm25Index | None = None
        self._chunks: dict[str, Chunk] = {}

    async def _keywords(self) -> Bm25Index:
        # Built on first use from what is in the store. Restart the app after re-ingesting.
        if self._keyword_index is None:
            self._chunks = {chunk.id: chunk for chunk in await self._store.all_chunks()}
            self._keyword_index = Bm25Index({i: chunk.text for i, chunk in self._chunks.items()})
        return self._keyword_index

    async def search(
        self,
        query: str,
        query_embedding: list[float],
        *,
        topic: str | None,
        method: Method = "hybrid",
    ) -> list[RetrievedChunk]:
        candidates = self._top_k * 3
        keyword_index = await self._keywords()

        # Below the similarity floor a chunk is "nearest" only because something has to be.
        semantic = [
            (chunk, similarity)
            for chunk, similarity in await self._store.nearest(
                query_embedding, limit=candidates, topic=topic
            )
            if similarity >= self._min_similarity
        ]
        if not semantic and method != "keyword":
            # Keyword matches alone are not evidence of relevance: any email shares a few
            # words with some page. Better no source than a wrong one.
            return []
        keyword = [
            self._chunks[chunk_id]
            # The index covers every topic, so ask for all matches and filter here.
            for chunk_id, _ in keyword_index.search(query, limit=len(self._chunks))
            if topic is None or self._chunks[chunk_id].topic == topic
        ][:candidates]
        if method == "semantic":
            keyword = []
        elif method == "keyword":
            semantic = []

        # Reciprocal rank fusion: a cosine similarity and a BM25 score are on different
        # scales and cannot be added, but ranks can. Each list gives 1 / (k + rank).
        chunks: dict[str, Chunk] = {}
        scores: dict[str, float] = {}
        semantic_ranks: dict[str, int] = {}
        keyword_ranks: dict[str, int] = {}
        similarities: dict[str, float] = {}
        for rank, (chunk, similarity) in enumerate(semantic, start=1):
            chunks[chunk.id] = chunk
            semantic_ranks[chunk.id], similarities[chunk.id] = rank, similarity
            scores[chunk.id] = scores.get(chunk.id, 0.0) + 1 / (_RRF_K + rank)
        for rank, chunk in enumerate(keyword, start=1):
            chunks[chunk.id] = chunk
            keyword_ranks[chunk.id] = rank
            scores[chunk.id] = scores.get(chunk.id, 0.0) + 1 / (_RRF_K + rank)

        best = sorted(scores, key=lambda chunk_id: -scores[chunk_id])[: self._top_k]
        return [
            RetrievedChunk(
                chunk=chunks[chunk_id],
                score=scores[chunk_id],
                semantic_rank=semantic_ranks.get(chunk_id),
                keyword_rank=keyword_ranks.get(chunk_id),
                similarity=similarities.get(chunk_id),
            )
            for chunk_id in best
        ]
