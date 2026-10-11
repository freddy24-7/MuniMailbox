"""Compare semantic, keyword and hybrid search on data/retrieval_testset.toml (FR-16).

    uv run python scripts/evaluate_retrieval.py            # as the app searches: within the topic
    uv run python scripts/evaluate_retrieval.py --no-topic # across the whole knowledge base

Needs ChromaDB (ingested) and the local embedding model. Calls no chat model.
"""

import argparse
import asyncio
from pathlib import Path

from app.config import get_settings
from app.rag.evaluation import METHODS, evaluate, load_cases, mean_reciprocal_rank, recall
from app.rag.retrieval import Retriever
from app.rag.store import ChromaVectorStore
from app.services import build_router


async def _main(testset: Path, use_topic: bool) -> None:
    settings = get_settings()
    cases = load_cases(testset)
    router, clients = build_router(settings)
    store = ChromaVectorStore(
        host=settings.chroma_host, port=settings.chroma_port, collection=settings.chroma_collection
    )
    retriever = Retriever(
        store, top_k=settings.retrieval_top_k, min_similarity=settings.retrieval_min_similarity
    )
    try:
        # The questions are made up for the test set: public text, not email text.
        results = await evaluate(retriever, router.embed_public, cases, use_topic=use_topic)
    finally:
        for client in clients:
            await client.aclose()

    scope = "within the topic" if use_topic else "whole knowledge base"
    print(f"{len(cases)} questions, top {settings.retrieval_top_k} passages, {scope}\n")
    print(f"{'method':10} {'recall@k':>9} {'MRR':>6}")
    for method in METHODS:
        found, rank = recall(results[method]), mean_reciprocal_rank(results[method])
        print(f"{method:10} {found:>8.0%} {rank:>6.2f}")
    print("\nNot found by hybrid search:")
    missed = [result for result in results["hybrid"] if result.first_hit is None]
    for result in missed:
        pages = sorted({url.rsplit("/", 1)[-1] for url in result.urls}) or ["nothing"]
        print(f"- {result.case.question}\n    got: {', '.join(pages)}")
    if not missed:
        print("- nothing")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--testset", type=Path, default=Path("data/retrieval_testset.toml"))
    parser.add_argument("--no-topic", action="store_true", help="search every topic")
    args = parser.parse_args()
    asyncio.run(_main(args.testset, not args.no_topic))
