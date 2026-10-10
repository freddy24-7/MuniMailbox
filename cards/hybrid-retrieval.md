# Hybrid retrieval (semantic + keyword)

**Status:** Overview · **Stages:** 4 · **Requirements:** FR-16 · **Code:** `app/rag/` (retrieval)

## In one sentence
Two different ways of searching the knowledge base — by meaning and by exact words — are
run side by side and their result lists are merged into one.

## Why it matters in this app
Citizens describe problems in everyday words ("my bin wasn't emptied"), which favours
semantic search. Regulations contain exact terms, form names and article numbers, which
favour keyword search. Each catches what the other misses.

## Core ideas
- **Dense (semantic) retrieval** — embeddings; finds paraphrases and related concepts,
  weak on rare exact terms and codes.
- **Sparse (keyword) retrieval** — term matching; **BM25** scores a chunk by how often the
  query words appear, weighted by how rare each word is and normalised for length.
- **"Two embedding approaches"** — the PRD's phrase. Strictly, BM25 is a ranking function
  over word counts, not a learned embedding; it can be seen as a very sparse vector.
- **Score incompatibility** — a cosine similarity and a BM25 score are on different
  scales and cannot simply be added.
- **Reciprocal rank fusion (RRF)** — merge by *rank* instead of score: each chunk gets
  `1 / (k + rank)` from each list, summed. Simple and robust.
- **Deduplication** — the same chunk found by both methods is one result with a higher score.
- **Evaluation** — recall@k and mean reciprocal rank on a small set of question → expected
  document pairs.
- **Dutch tokenization** — compound words and stop words affect keyword search more than
  semantic search.

## In this project
- Two collections, results merged (FR-16).
- "Comparison mode for the two embedding approaches" is a later feature — worth keeping
  the two retrievers separately callable from the start.

## Questions to be able to answer
- Give one query where semantic search wins and one where BM25 wins.
- Why does RRF not need the scores to be comparable?
- How would you show that hybrid beats either method alone on this knowledge base?
- What should the merged result keep so the comparison mode is possible later?

## Related cards
[rag-ingestion-and-embeddings](rag-ingestion-and-embeddings.md) · [grounded-drafting-and-quality](grounded-drafting-and-quality.md) · [testing-strategy](testing-strategy.md)

## Notes from the build
_Empty — filled in during Stage 4._
