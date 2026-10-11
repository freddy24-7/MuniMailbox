"""Measure retrieval on questions whose right page is known (FR-16).

A case is a question and the pages that answer it. Two numbers per method:
- recall@k: the share of questions for which a right page is among the k passages;
- MRR (mean reciprocal rank): 1 / the position of the first right passage, averaged.
  1.0 means always first, 0.5 means second on average, 0 means never found.
"""

import tomllib
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from app.rag.retrieval import Method, Retriever

METHODS: tuple[Method, ...] = ("semantic", "keyword", "hybrid")
EmbedQuestions = Callable[[list[str]], Awaitable[list[list[float]]]]


@dataclass(frozen=True)
class RetrievalCase:
    question: str
    topic: str
    expected_urls: frozenset[str]


@dataclass(frozen=True)
class CaseResult:
    case: RetrievalCase
    # Position (1 = first) of the first passage from an expected page, or None.
    first_hit: int | None
    urls: tuple[str, ...]


def load_cases(path: Path) -> list[RetrievalCase]:
    return [
        RetrievalCase(case["question"], case["topic"], frozenset(case["expected"]))
        for case in tomllib.loads(path.read_text(encoding="utf-8"))["case"]
    ]


def first_hit(urls: Sequence[str], expected: frozenset[str]) -> int | None:
    return next((rank for rank, url in enumerate(urls, start=1) if url in expected), None)


def recall(results: Sequence[CaseResult]) -> float:
    return sum(result.first_hit is not None for result in results) / max(len(results), 1)


def mean_reciprocal_rank(results: Sequence[CaseResult]) -> float:
    return sum(1 / result.first_hit for result in results if result.first_hit) / max(
        len(results), 1
    )


async def evaluate(
    retriever: Retriever,
    embed: EmbedQuestions,
    cases: Sequence[RetrievalCase],
    *,
    use_topic: bool = True,
) -> dict[Method, list[CaseResult]]:
    """Run every case with every method. Each question is embedded once."""
    embeddings = await embed([case.question for case in cases])
    results: dict[Method, list[CaseResult]] = {method: [] for method in METHODS}
    for case, embedding in zip(cases, embeddings, strict=True):
        for method in METHODS:
            hits = await retriever.search(
                case.question,
                embedding,
                topic=case.topic if use_topic else None,
                method=method,
            )
            urls = tuple(hit.chunk.url for hit in hits)
            results[method].append(CaseResult(case, first_hit(urls, case.expected_urls), urls))
    return results
