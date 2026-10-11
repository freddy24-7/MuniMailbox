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
- ~~No measurement yet.~~ Measured the same day; see below.
- The keyword index is built once per process: restart the app after re-ingesting.
- Dutch compounds are not split, so "afvalcontainer" does not match "container".
- ~~The comparison mode is not built.~~ `Retriever.search(method=...)` runs one method alone.

### Stage 4 — 2026-10-11 (measured)

**What was built**
- [data/retrieval_testset.toml](../data/retrieval_testset.toml): 29 made-up citizen
  questions in everyday words, each with the page or pages that answer it.
- [app/rag/evaluation.py](../app/rag/evaluation.py) and
  [scripts/evaluate_retrieval.py](../scripts/evaluate_retrieval.py): run every question
  through semantic, keyword and hybrid search and report recall@4 and MRR.

**Seen in practice**

| Method | Recall@4, within the topic | MRR | Recall@4, whole knowledge base | MRR |
| --- | --- | --- | --- | --- |
| Semantic | 83% | 0.78 | 83% | 0.74 |
| Keyword | 79% | 0.73 | 72% | 0.61 |
| Hybrid | 83% | 0.75 | 83% | 0.74 |

- **Hybrid is not better than semantic search alone on this test set.** It finds the same
  number of right pages and ranks them slightly lower within a topic. The assumption this
  card started with did not survive its first measurement.
- **The topic filter helps keyword search most** (72% → 79%): it removes pages that share
  words but are about something else.
- **Four of the five misses returned nothing at all.** The cause is the similarity floor,
  not the search. Measured best similarity per question:
  - questions that have an answer: 0.53 to 0.74;
  - questions that have none (passport, wedding, swimming pool): 0.41 to 0.59.

  The two ranges overlap, so no floor separates them:

  | Floor | Recall@4 (within topic) | Off-topic questions that still get a source |
  | --- | --- | --- |
  | 0.55 (current) | 83% | 1 of 6 |
  | 0.50 | 93% | 4 of 6 |
  | 0.40 | 90% | 6 of 6 |

- The grey-container question is still a miss at every floor: it gets the pages about
  underground containers and fines, not "Kliko's" or "Ophaaldag".

**Still open**
- Which floor: more answers found, or fewer wrong sources? Left at 0.55 until decided.
- 29 questions and 6 off-topic ones, written by one person from page titles. Enough to
  see a pattern, too few to trust a difference of a few points.
- Why hybrid does not help here: untested ideas are the stop-word list, unsplit Dutch
  compounds, and questions deliberately written without the pages' own terms.
