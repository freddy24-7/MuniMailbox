# RAG: ingestion, chunking and embeddings

**Status:** Overview · **Stages:** 4 · **Requirements:** FR-7, FR-15, FR-17 · **Code:** `app/rag/`, `scripts/ingest_documents.py`

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
_Empty — filled in during Stage 4._
