# RAG: ingestion, chunking and embeddings

**Status:** Stage 4 notes added, questions open · **Stages:** 4 · **Requirements:** FR-7, FR-15, FR-17 · **Code:** `app/rag/`, `scripts/ingest_documents.py`

## In one sentence
Instead of hoping the model knows the municipality's rules, the relevant passages are
looked up in a prepared knowledge base and placed in the prompt, so the answer can cite them.

## Why it matters in this app
A reply about a permit or a benefit must match the actual regulation and be verifiable.
Retrieval grounds the draft in documents the caseworker can open (goal 2), and it lets a
small local model answer questions it was never trained on.

## Core ideas
- **Retrieval-augmented generation** — retrieve first, then generate using what was retrieved.
- **Ingestion pipeline** — load (HTML, PDF, text) → clean → chunk → embed → store.
- **Chunking** — splitting documents into passages. Too large: irrelevant text dilutes
  the prompt. Too small: the answer is cut from its context. Overlap and splitting on
  headings help.
- **Embedding** — a vector of numbers that represents meaning; similar texts get nearby vectors.
- **Vector store** — a database that finds the nearest vectors to a query vector (ChromaDB).
- **Similarity and top-k** — cosine similarity ranks chunks; the k best are returned.
- **Metadata** — title, URL, topic and date stored with each chunk, used for filtering and citing.
- **Multilingual embeddings** — the documents and questions are in Dutch, so the embedding
  model must handle Dutch well.
- **Ingestion is offline, retrieval is online** — the slow part happens once, ahead of time.

## In this project
- `uv run python scripts/ingest_documents.py data/documents/`
- The policy agent retrieves chunks for the email's topic and question (FR-7).
- Every draft lists its documents with title and link (FR-17).
- Fetch date stored per document; re-ingest script for stale sources (risk table).
- The query sent to retrieval is built from the *redacted* email.

## Questions to be able to answer
- How do you choose a chunk size, and how do you know the choice was good?
- Why store the topic as metadata when the embedding already "knows" the topic?
- What happens if no chunk is really relevant — and how can the system notice?
- Why must the same embedding model be used for ingestion and for queries?
- How would you build a small test set to measure retrieval quality?

## Related cards
[hybrid-retrieval](hybrid-retrieval.md) · [grounded-drafting-and-quality](grounded-drafting-and-quality.md) · [agent-orchestration](agent-orchestration.md)

## Notes from the build
### Stage 4 — 2026-10-11

**What was built**
- [data/sources.toml](../data/sources.toml): the knowledge base as a list of 36 public
  pages of the municipality of Utrecht, grouped by topic. Only the list is in the
  repository; the page texts are git-ignored.
- [scripts/fetch_documents.py](../scripts/fetch_documents.py) downloads them;
  [app/rag/html.py](../app/rag/html.py) turns a page into plain text with its headings kept.
- [app/rag/chunking.py](../app/rag/chunking.py): one chunk per section, at most
  `CHUNK_MAX_CHARS` (1200) characters.
- [app/rag/ingestion.py](../app/rag/ingestion.py) and
  [scripts/ingest_documents.py](../scripts/ingest_documents.py): chunk → embed → store.
- [app/rag/store.py](../app/rag/store.py): the `VectorStore` interface, with ChromaDB for
  the running app and an in-memory version for tests.
- Embedding model: `bge-m3` through Ollama, the same local server as the chat model.

**Decisions and why**
- **Split on headings, not on a character count.** A municipal page is already organised
  as question-sized sections ("Kosten", "Aanvragen"). A long section is split between
  paragraphs, and the last paragraph is repeated at the start of the next chunk.
- **Every chunk starts with the page title and its heading.** "Dit kost € 25" means
  nothing alone; "Grofvuil - Kosten / Dit kost € 25" can be found and understood.
- **Metadata on every chunk**: title, heading, URL, topic, fetch date. The topic is used
  to filter, the rest to cite. The fetch date is shown with each citation so a stale
  source is visible.
- **Re-ingesting a page replaces its chunks** (`replace_url`). The script can be run again
  when pages change without leaving old passages behind.
- **Document text has its own type, `PublicText`.** The router sends it to a model without
  the redaction check, because a public page legitimately contains words that would be
  personal data in an email (a street, a city). Email text can never take that route.
- **Embeddings are always computed locally.** There is no cloud route for them.
- The HTML extractor uses only the standard library; no new dependency for parsing.

**Seen in practice**
- 36 pages became 434 chunks, about 12 per page.
- The pages come from two sites with different HTML. The extractor looks for `<article>`,
  then `<main>`, then `<div id="main">`, then falls back to `<body>`.
- Ingestion embeds every chunk once (slow, offline). Answering an email embeds one query.

**Try it**
- Open a file in `data/documents/` next to its page in the browser: what was dropped?
- `uv run pytest tests/unit/test_rag.py -v` (no models or containers needed).

**Still open**
- Chunk size 1200 is a reasoned guess, not a measured optimum. There is no retrieval test
  set yet to compare sizes with.
- Only HTML. PDFs (regulations) are not ingested.
- The knowledge base is one real municipality standing in for the fictional one, so
  drafts quote Utrecht's amounts and phone numbers.
