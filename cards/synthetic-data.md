# Synthetic data

**Status:** Overview · **Stages:** 1, 3 · **Requirements:** GR-9, GR-4 · **Code:** `scripts/generate_synthetic.py`, `tests/pii_testset/`

## In one sentence
Every citizen, case and email in the project is generated, never real, yet realistic
enough that the pipeline behaves as it would on real mail.

## Why it matters in this app
It removes the legal problem (no real personal data anywhere) and gives something real
data cannot: **ground truth**. Because the generator knows exactly which PII it put in each
email, redaction recall can be measured.

## Core ideas
- **Faker with the `nl_NL` locale** — Dutch names, addresses, postcodes, phone numbers.
- **Structurally valid identifiers** — a generated BSN must pass the 11-check and a
  generated IBAN the mod-97 check, or the recognizers are never really tested.
- **Seeded generation** — the same seed gives the same dataset, so tests are reproducible.
- **Labelled data** — each email is stored with the list of PII spans it contains.
- **Template vs LLM-written text** — templates give exact labels; LLM-written emails give
  natural variety but need the PII inserted afterwards so labels stay exact.
- **Coverage** — all five topics plus "other", all urgency levels, threats and distress,
  messy formats (phone numbers with spaces, postcodes without).

## In this project
- `uv run python scripts/generate_synthetic.py --citizens 200 --emails 500`
- Feeds three things: the mock case database, the simulated inbox, and the PII test set.
- Rule: never add real names, BSNs, addresses or emails to code, fixtures, tests, prompts or docs.

## Questions to be able to answer
- Why can't recall be measured on emails whose PII is not labelled?
- How could "synthetic" data still accidentally match a real person, and does it matter?
- What makes a test email *hard* for a redactor? Can the generator produce those on purpose?
- If an LLM writes the email texts, which rule from CLAUDE.md applies to that call?

## Related cards
[pii-redaction](pii-redaction.md) · [testing-strategy](testing-strategy.md) · [data-model-and-migrations](data-model-and-migrations.md)

## Notes from the build
_Empty — filled in during Stage 1._
