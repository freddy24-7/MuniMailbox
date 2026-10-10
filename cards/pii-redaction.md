# PII detection and redaction

**Status:** Overview · **Stages:** 2, 3 · **Requirements:** FR-5, GR-1, GR-4 · **Code:** `app/privacy/`, `app/agents/privacy.py`

## In one sentence
Before any model sees an email, every piece of personal data is found and swapped for a
typed placeholder such as `[PERSON_1]`, and the swap is recorded so it can be undone later.

## Why it matters in this app
This is the biggest risk in the PRD: one missed entity means personal data could reach a
cloud model. Redaction is the first pipeline step and the precondition for every model call.

## Core ideas
- **PII entity types** — names, addresses, postcodes, BSN, IBAN, phone, email, date of birth.
- **Presidio** — an *analyzer* finds entities, an *anonymizer* replaces them.
- **Recognizer kinds** — pattern (regex), checksum-validated pattern, and NER (a language
  model that tags names and places). Each has different failure modes.
- **Checksum validation** — a BSN is 9 digits that pass the "11-check" (weighted sum
  divisible by 11); an IBAN passes mod-97. A checksum turns "any 9 digits" into a
  high-confidence match.
- **Context words** — "BSN", "burgerservicenummer", "geboren op" near a match raise its score.
- **Typed, numbered placeholders** — `[PERSON_1]`, `[PERSON_2]`: the same value always gets
  the same placeholder within an email, so the text still makes sense to the model.
- **Pseudonymization vs anonymization** — because a mapping exists, this is pseudonymization
  in GDPR terms. The redacted text alone is what leaves the machine.
- **Recall vs precision** — recall = share of real PII found. A miss is a leak; a false
  positive only makes a draft slightly worse. So recall is gated.
- **Redaction record** — proof that an email was processed, which the router demands.

## In this project
- Custom Dutch recognizers: BSN with 11-check, postcode (`1234 AB`), IBAN, Dutch phone formats.
- Redaction runs before *any* LLM call, local as well as cloud (GR-1).
- A test set with known PII measures recall; the build fails below the threshold (GR-4).

## Questions to be able to answer
- Why redact for the local model too, when the data never leaves the machine?
- Which entity types are easy (pattern) and which are hard (names, addresses)? Why?
- What can still identify a person after all listed entities are removed?
- Why must placeholders be consistent within one email?
- What exactly goes in a redaction record?

## Related cards
[encryption-and-retention](encryption-and-retention.md) · [inference-routing](inference-routing.md) · [synthetic-data](synthetic-data.md) · [testing-strategy](testing-strategy.md)

## Notes from the build
_Empty — filled in during Stage 2._
