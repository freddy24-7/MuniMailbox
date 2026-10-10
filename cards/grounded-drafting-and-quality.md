# Grounded drafting, prompts and quality checks

**Status:** Stage 2 notes added, questions open · **Stages:** 2 (basic draft), 4 · **Requirements:** FR-6, FR-10, FR-11, FR-17 · **Code:** `prompts/`, `app/agents/drafting.py`, `app/agents/quality.py`, `app/agents/intake.py`

## In one sentence
The model is given the redacted email, the retrieved sources and the case context, is
told to answer only from those sources and cite them, and a second check looks for claims
the sources do not support.

## Why it matters in this app
"Hallucinated rules in drafts" is a named risk: a confident, wrong answer about a benefit
deadline is worse than no draft. Grounding plus checking plus human approval are the
three layers against it.

## Core ideas
- **Grounding** — the answer must be derivable from the provided context, not from the
  model's memory.
- **Hallucination** — fluent output that is not supported by any source.
- **Prompt template** — a versioned file with slots for email, sources and case context;
  kept in `prompts/`, not inline in code.
- **Structured output** — asking for JSON (reply text + list of source IDs used) so the
  result can be validated instead of parsed by hope.
- **Citation by ID** — sources are numbered in the prompt; the model returns IDs, and the
  code maps IDs to title and URL. The model never writes a URL itself.
- **Citation validation** — any cited ID that was not in the retrieved set is rejected.
- **Few-shot examples** — sample email/reply pairs that show tone and structure; one of
  the mitigations for a small model writing weak Dutch.
- **LLM-as-judge** — a second model call that critiques the draft (tone, completeness,
  unsupported claims, contradictions). Useful, not infallible.
- **Classification with a closed label set** — the intake agent must return one of six
  topics and one of three urgency levels, nothing else.
- **Prompt injection** — an email is untrusted input; it may contain text that tries to
  instruct the model.

## In this project
- One LLM call for the draft (FR-10), in Dutch, with source references.
- Quality warnings are attached to the draft and shown to the caseworker (FR-11, FR-19).
- NFR: 100% of drafts have at least one valid source reference.
- The model works with placeholders: the draft says `Geachte [PERSON_1]`.

## Questions to be able to answer
- How does the code guarantee the model cannot invent a source?
- What should happen when retrieval returns nothing relevant?
- What can a quality agent catch that a rule-based check cannot, and the other way round?
- How do you change a prompt and know it got better rather than different?
- What would a prompt-injection email look like here, and what limits the damage?

## Related cards
[rag-ingestion-and-embeddings](rag-ingestion-and-embeddings.md) · [hybrid-retrieval](hybrid-retrieval.md) · [inference-routing](inference-routing.md) · [human-in-the-loop-review](human-in-the-loop-review.md)

## Notes from the build
### Stage 2 — 2026-10-10 (a first prompt, no grounding yet)

**What was built**
- [prompts/draft_reply.v1.md](../prompts/draft_reply.v1.md): the first prompt, in Dutch.
  Above the `---USER---` line are the instructions (system part); below it the email.
- [app/agents/drafting.py](../app/agents/drafting.py): one model call per email.

**Decisions and why**
- There is no knowledge base yet, so the prompt tells the model **not** to state rules,
  amounts or deadlines, and to write a holding reply instead: confirm receipt, summarise
  the question, say a colleague will look into it.
- The prompt explains placeholders and tells the model to copy them exactly and invent none.
- The prompt says the email is information, not instructions (prompt injection).
- The template is split into system and user parts *before* the email text is inserted,
  so an email containing `---USER---` cannot move itself into the instructions.
- The version is in the file name. A changed prompt becomes `v2`, and `Draft.model_used`
  plus the prompt name tell you later what produced a draft.

**Seen in practice**
- `gemma3:4b` kept every placeholder intact and followed the "do not invent rules"
  instruction. The Dutch is usable but stiff ("Wij zullen uw vraag nagaan en een
  medewerker dit uitzoeken").
- It opened with "Geachte heer/mevrouw [PERSON_1]", copying the citizen's own salutation.

**Not met yet (Stage 4)**
- Sources in the prompt, citation by ID, structured output, the quality agent, few-shot examples.
