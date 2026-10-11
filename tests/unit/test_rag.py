import tomllib
from itertools import pairwise
from pathlib import Path

import pytest

from app.rag.bm25 import Bm25Index, tokenize
from app.rag.chunking import Chunk, Document, chunk_document
from app.rag.evaluation import (
    METHODS,
    CaseResult,
    RetrievalCase,
    evaluate,
    first_hit,
    load_cases,
    mean_reciprocal_rank,
    recall,
)
from app.rag.html import extract
from app.rag.ingestion import ingest
from app.rag.retrieval import Retriever
from app.rag.store import InMemoryVectorStore
from tests.helpers import FakeEmbedder

URL = "https://gemeente.example/afval/grofvuil"


def _document(text: str, title: str = "Grofvuil") -> Document:
    return Document(
        url=URL, title=title, topic="waste_collection", fetched_at="2026-10-01", text=text
    )


def _chunk(chunk_id: str, text: str, topic: str = "waste_collection") -> Chunk:
    return Chunk(
        id=chunk_id,
        text=text,
        title="Titel",
        heading="",
        url=f"https://gemeente.example/{chunk_id}",
        topic=topic,
        fetched_at="2026-10-01",
    )


# --- HTML to text (FR-15) ---


def test_extract_keeps_headings_and_drops_navigation_and_scripts() -> None:
    html = """
    <html><head><title>Grofvuil | Gemeente</title><script>track()</script></head>
    <body><nav><a href="/">Home</a></nav>
    <main><h1>Grofvuil</h1><p>Maak een   afspraak.</p>
    <h2>Kosten</h2><ul><li>Eerste keer gratis</li></ul>
    <form><button>Zoek</button></form></main>
    <footer>Contact</footer></body></html>
    """
    title, text = extract(html)

    assert title == "Grofvuil"
    assert text.splitlines() == [
        "# Grofvuil",
        "Maak een afspraak.",
        "## Kosten",
        "- Eerste keer gratis",
    ]


def test_extract_falls_back_to_the_title_tag() -> None:
    title, _ = extract(
        "<html><head><title>Ophaaldag | Gemeente</title></head><body><p>Tekst</p></body></html>"
    )
    assert title == "Ophaaldag"


# --- Chunking ---


def test_each_section_becomes_a_chunk_that_starts_with_title_and_heading() -> None:
    chunks = chunk_document(
        _document("# Grofvuil\nMaak een afspraak.\n## Kosten\nDit kost € 25."), max_chars=1200
    )

    assert [chunk.text for chunk in chunks] == [
        "Grofvuil\nMaak een afspraak.",
        "Grofvuil - Kosten\nDit kost € 25.",
    ]
    assert [chunk.heading for chunk in chunks] == ["Grofvuil", "Kosten"]
    assert all((chunk.url, chunk.topic) == (URL, "waste_collection") for chunk in chunks)


def test_a_long_section_is_split_with_one_paragraph_of_overlap() -> None:
    paragraphs = [f"Alinea {number} " + "x" * 40 for number in range(6)]
    chunks = chunk_document(_document("## Regels\n" + "\n".join(paragraphs)), max_chars=200)

    assert len(chunks) > 1
    for previous, following in pairwise(chunks):
        assert following.text.splitlines()[1] == previous.text.splitlines()[-1]
    # Nothing is lost.
    assert all(any(paragraph in chunk.text for chunk in chunks) for paragraph in paragraphs)


def test_chunk_ids_are_stable_and_unique() -> None:
    document = _document("## A\nEen.\n## B\nTwee.")
    first = [chunk.id for chunk in chunk_document(document, max_chars=1200)]
    again = [chunk.id for chunk in chunk_document(document, max_chars=1200)]

    assert first == again
    assert len(set(first)) == 2


# --- Keyword search (FR-16) ---


def test_tokenize_drops_stopwords_and_case() -> None:
    assert tokenize("Geachte heer, ik wil de Kliko omruilen!") == ["kliko", "omruilen"]


def test_bm25_finds_the_exact_term_and_skips_documents_without_it() -> None:
    index = Bm25Index(
        {
            "kliko": "Een kliko omruilen kan via het formulier.",
            "grofvuil": "Grofvuil laten ophalen kost geld.",
            "parkeren": "Een parkeervergunning aanvragen.",
        }
    )

    assert [doc_id for doc_id, _ in index.search("Mijn kliko is kapot", limit=5)] == ["kliko"]
    assert index.search("paspoort verlengen", limit=5) == []


def test_bm25_ranks_a_rare_word_above_a_common_one() -> None:
    index = Bm25Index(
        {
            "a": "afval afval afval ophaaldag",
            "b": "afval inkomenstoeslag",
            "c": "afval container",
        }
    )
    assert index.search("afval inkomenstoeslag", limit=1)[0][0] == "b"


# --- Hybrid retrieval ---


async def _store(*items: tuple[Chunk, list[float]]) -> InMemoryVectorStore:
    store = InMemoryVectorStore()
    for chunk, embedding in items:
        await store.replace_url(chunk.url, [chunk], [embedding])
    return store


async def test_a_chunk_found_by_both_methods_ranks_first() -> None:
    store = await _store(
        (_chunk("meaning", "Grote spullen kwijt? Wij komen langs."), [1.0, 0.0]),
        (_chunk("both", "Grofvuil laten ophalen: maak een afspraak."), [0.9, 0.1]),
    )
    hits = await Retriever(store, top_k=2, min_similarity=0.5).search(
        "Hoe laat ik grofvuil ophalen?", [1.0, 0.0], topic=None
    )

    assert [hit.chunk.id for hit in hits] == ["both", "meaning"]
    assert (hits[0].semantic_rank, hits[0].keyword_rank) == (2, 1)
    assert hits[1].semantic_rank == 1


async def test_keyword_matches_alone_are_not_returned() -> None:
    """Better no source than a wrong one: nothing is similar in meaning, so nothing counts."""
    store = await _store((_chunk("far", "Grofvuil laten ophalen."), [0.0, 1.0]))
    hits = await Retriever(store, top_k=2, min_similarity=0.5).search(
        "grofvuil", [1.0, 0.0], topic=None
    )
    assert hits == []


async def test_search_stays_inside_the_topic() -> None:
    store = await _store(
        (_chunk("waste", "Grofvuil laten ophalen."), [1.0, 0.0]),
        (_chunk("permit", "Grofvuil en vergunningen.", topic="permits"), [1.0, 0.0]),
    )
    retriever = Retriever(store, top_k=5, min_similarity=0.5)

    in_topic = await retriever.search("grofvuil", [1.0, 0.0], topic="permits")
    everywhere = await retriever.search("grofvuil", [1.0, 0.0], topic=None)

    assert [hit.chunk.id for hit in in_topic] == ["permit"]
    assert {hit.chunk.id for hit in everywhere} == {"waste", "permit"}


# --- Ingestion ---


async def test_ingesting_a_page_again_replaces_its_old_chunks() -> None:
    store, embedder = InMemoryVectorStore(), FakeEmbedder()

    first = await ingest(
        [_document("## A\nOud.\n## B\nOok oud.")], store, embedder.embed, max_chars=1200
    )
    second = await ingest([_document("## A\nNieuw.")], store, embedder.embed, max_chars=1200)

    assert (first, second) == (2, 1)
    assert [chunk.text for chunk in await store.all_chunks()] == ["Grofvuil - A\nNieuw."]


# --- Measuring retrieval ---

WASTE = "https://gemeente.example/waste"
PERMIT = "https://gemeente.example/permit"


def test_first_hit_is_the_position_of_the_first_passage_from_an_expected_page() -> None:
    assert first_hit(["a", "b", "c"], frozenset({"b", "c"})) == 2
    assert first_hit(["a", "b"], frozenset({"x"})) is None
    assert first_hit([], frozenset({"x"})) is None


def test_recall_and_mean_reciprocal_rank() -> None:
    case = RetrievalCase("vraag", "waste_collection", frozenset({"x"}))
    results = [CaseResult(case, 1, ()), CaseResult(case, 4, ()), CaseResult(case, None, ())]

    assert recall(results) == pytest.approx(2 / 3)
    assert mean_reciprocal_rank(results) == pytest.approx((1 + 1 / 4 + 0) / 3)
    assert (recall([]), mean_reciprocal_rank([])) == (0, 0)


async def test_each_method_can_be_measured_on_its_own() -> None:
    """A question whose words are on one page and whose meaning is on another: the two
    methods disagree, and the evaluation shows which found the right page."""
    store = await _store(
        (_chunk("waste", "De kliko wordt op maandag geleegd."), [1.0, 0.0]),
        (_chunk("permit", "Grijze container vergunning.", topic="permits"), [0.0, 1.0]),
    )
    embedder = FakeEmbedder()
    embedder.vectors = {"Mijn grijze container is niet geleegd.": [1.0, 0.0]}
    case = RetrievalCase(
        "Mijn grijze container is niet geleegd.",
        "waste_collection",
        frozenset({WASTE}),
    )
    retriever = Retriever(store, top_k=1, min_similarity=0.5)

    everywhere = await evaluate(retriever, embedder.embed, [case], use_topic=False)
    in_topic = await evaluate(retriever, embedder.embed, [case], use_topic=True)

    assert [everywhere[method][0].first_hit for method in METHODS] == [1, None, 1]
    assert everywhere["keyword"][0].urls == (PERMIT,)
    assert [in_topic[method][0].first_hit for method in METHODS] == [1, 1, 1]
    assert len(embedder.calls) == 2  # each question is embedded once, not once per method


def test_the_retrieval_test_set_only_expects_pages_that_are_in_the_knowledge_base() -> None:
    root = Path(__file__).resolve().parents[2] / "data"
    sources = tomllib.loads((root / "sources.toml").read_text(encoding="utf-8"))["source"]
    by_topic = {group["topic"]: set(group["urls"]) for group in sources}
    cases = load_cases(root / "retrieval_testset.toml")

    assert len(cases) >= 25
    for case in cases:
        # Within its own topic too, or the case can never be found the way the app searches.
        assert case.expected_urls & by_topic[case.topic], case.question
        assert case.expected_urls <= set().union(*by_topic.values()), case.question
