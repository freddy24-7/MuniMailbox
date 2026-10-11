# Hybrid retrieval (semantic + keyword)

**Status:** Stage 4 notes added, questions open · **Stages:** 4 · **Requirements:** FR-16 · **Code:** `app/rag/` (retrieval)

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
### Stage 4 — 2026-10-11

**What was built**
- [app/rag/bm25.py](../app/rag/bm25.py): BM25 keyword search in about 60 lines of plain
  Python, with a Dutch stop-word list.
- [app/rag/retrieval.py](../app/rag/retrieval.py): the `Retriever`. It asks the vector
  store for the nearest chunks, asks BM25 for the best keyword matches, and merges the
  two lists with reciprocal rank fusion.
- [app/agents/policy.py](../app/agents/policy.py): the policy agent. Its query is the
  redacted subject and body; its result is stored on the email as numbered passages.

**Decisions and why**
- **One collection plus an index, not two collections.** The PRD says "two embedding
  collections". BM25 is not an embedding, so the chunks are stored once in ChromaDB and
  the keyword index is built in memory from the same chunks when the app first searches.
  This is a deviation from the PRD's wording, not from its intent.
- **Merged by rank (RRF, k = 60).** A cosine similarity (0 to 1) and a BM25 score
  (unbounded) cannot be added. A rank can: each list gives a chunk `1 / (60 + rank)`.
- **A similarity floor** (`RETRIEVAL_MIN_SIMILARITY`, 0.55). With `bge-m3`, related text
  scored 0.65 or more and unrelated text about 0.5. Below the floor a chunk is "nearest"
  only because something has to be.
- **Keyword matches alone are not returned.** If nothing passes the floor, the result is
  empty even when BM25 found words in common. Any email shares a few words with some
  page; better no source than a wrong one.
- **Filtered by the intake agent's topic.** Topic "other" searches everything.
- **Both ranks are kept** on every passage (`semantic_rank`, `keyword_rank`) and shown on
  the review page, so the two methods can be compared later.

**Seen in practice**
- One real email, "Container niet geleegd" (a grey container was not emptied), got these
  four passages:

  | # | Semantic rank | Keyword rank |
  | --- | --- | --- |
  | 1 | 1 | 7 |
  | 2 | 4 | 5 |
  | 3 | 3 | 8 |
  | 4 | — | 1 |

  Passage 4 was found by keywords only, and it was the best keyword match. Passage 1 was
  the best by meaning and only seventh by keywords. Neither method alone would have
  produced this list.
- All four came from the page about **underground** containers. The email is about a grey
  wheelie bin, and the knowledge base has pages "Kliko's" and "Ophaaldag". This looks
  like a retrieval miss: the citizen wrote "container", the right page says "kliko".
  One email is not a measurement, but it is the first entry for a retrieval test set.

**Try it**
- Open an email's detail page and unfold "All passages the model was given": the ranks
  are next to each passage.
- In `tests/unit/test_rag.py`, read `test_keyword_matches_alone_are_not_returned` and
  `test_a_chunk_found_by_both_methods_ranks_first`.

**Still open**
- No measurement yet (recall@k on question → expected page pairs). Until there is one,
  "hybrid is better" is an assumption.
- The keyword index is built once per process: restart the app after re-ingesting.
- Dutch compounds are not split, so "afvalcontainer" does not match "container".
- The comparison mode itself (run one method only) is not built.
