from itertools import pairwise

from app.rag.bm25 import Bm25Index, tokenize
from app.rag.chunking import Chunk, Document, chunk_document
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
