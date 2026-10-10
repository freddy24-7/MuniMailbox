# Synthetic data

**Status:** Stage 1 notes added, questions open · **Stages:** 1, 3 · **Requirements:** GR-9, GR-4 · **Code:** `scripts/generate_synthetic.py`, `tests/pii_testset/`

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
### Stage 1 — 2026-10-10

**What was built**
- [scripts/generate_synthetic.py](../scripts/generate_synthetic.py). `generate()` is pure
  (no database, no files), which is what makes it testable; `persist()` and `write_feed()`
  do the I/O.
- Citizens, cases and seven demo users go to the database. Emails go to
  `data/synthetic/emails.jsonl`, one JSON object per line, each with its ground truth:
  topic, urgency, sensitive flag and a list of PII spans.
- `render()` fills a template slot by slot and records `start`, `end`, entity type and
  value for every personal slot. The labels are exact because they are produced by the
  same step that inserts the value.

**Decisions and why**
- Emails are a feed file, not database rows: the stored original must be encrypted, and
  the intake step that does that arrives in Stage 2.
- `fake.unique.ssn()` gives BSNs that pass the 11-check; a test verifies this with our own
  validator in [app/privacy/checksums.py](../app/privacy/checksums.py).
- `safe_email()` only uses the reserved `example.com/.org/.net` domains, so a generated
  address can never be someone's real mailbox.
- One `random.Random(seed)` drives everything, and "now" is the fixed `REFERENCE_NOW`, so
  `--seed 1` gives the same dataset on any day.

**Seen in practice**
- Faker's Dutch formats are already messy in a useful way: postcodes come out as `8928LP`
  (no space) and phones as `+31093 638962`. The recognizers in Stage 3 must cope.
- Cities are inserted but **not labelled** as PII; only street address and postcode are.
- Limitation: 12 templates produce all 500 emails, so the texts are repetitive. Recall and
  classification measured on them will look better than they would on real mail.

**Try it**
- `head -1 data/synthetic/emails.jsonl | python3 -m json.tool`
- Run twice with `--seed 1 --out /tmp/a.jsonl` and `/tmp/b.jsonl`, then `diff` the two files.
  (Use a fresh database or expect a unique-BSN error on the second run — why?)

**Not met yet**
- Hard cases on purpose: PII in the subject, two people in one email, a BSN written with
  dots, a name that is also an ordinary word (Stage 3, with the PII test set).
- LLM-written email texts for variety.
