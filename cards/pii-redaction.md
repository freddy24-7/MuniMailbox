# PII detection and redaction

**Status:** Stage 3 notes added, questions open · **Stages:** 2, 3 · **Requirements:** FR-5, GR-1, GR-4 · **Code:** `app/privacy/`, `app/agents/privacy.py`

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
### Stage 2 — 2026-10-10

**What was built**
- [app/privacy/engine.py](../app/privacy/engine.py): Presidio with the Dutch spaCy model
  `nl_core_news_lg` for names and places, the built-in email, IBAN and phone recognizers,
  and three of our own. `ENTITY_MAP` decides which detections are redacted and as what.
- [app/privacy/recognizers.py](../app/privacy/recognizers.py): BSN (nine digits **plus**
  the 11-check), postcode, numeric date.
- [app/privacy/redaction.py](../app/privacy/redaction.py): `Redactor.redact()` turns
  detections into numbered placeholders and returns the mapping.
- [app/agents/privacy.py](../app/agents/privacy.py): stores the redacted text, the
  encrypted mapping and a `RedactionRecord` (counts per type, never values).

**Decisions and why**
- Subject and body are redacted in one call with one shared mapping, so `[PERSON_1]` is
  the same person in both.
- **Propagation:** a value detected once is replaced everywhere it occurs. NER depends on
  context and often finds a name in the signature but misses it in the first line.
- Overlapping detections: the most confident wins, then the longest.
- Relative dates ("volgende maand") and organisations are not redacted: they identify
  nobody and the draft needs them.
- Presidio runs in a thread (`asyncio.to_thread`) because it is synchronous. It takes
  about 17 ms per email; loading the model takes about a second, once, at startup.

**Seen in practice — baseline on 150 synthetic emails**

| Type | Fully redacted | How it is detected |
| --- | --- | --- |
| BSN, IBAN, email, postcode, date of birth | all | pattern, some with checksum |
| Names | 121 of 168 (25 more partially) | NER |
| Phone numbers | 47 of 64 | built-in pattern, not Dutch-specific |
| Street addresses | 5 of 94 | NER, which does not know street names |

- In the first live run "Pleunsteeg 8" and the phone number reached the local model.
  Pattern-based types are solved; NER-based types are where recall is lost.
- Cities are redacted as `[ADDRESS_n]` even though the generator does not label them.
- Double-barrelled names are typically caught only in part ("Kayleigh" out of a longer name).

**Try it**
- In the app, open an email and expand "What the model saw". Find something that should
  have been a placeholder and is not.
- `uv run pytest tests/unit/test_redaction.py -v` and read the test names.

**Not met yet (Stage 3)**
- Street-address and Dutch phone recognizers; the labelled test set and the recall gate in CI.
- Using what the municipality already knows about the sender (name, address) as extra detection.

### Stage 3 — 2026-10-10

**What was built**
- [app/privacy/recognizers.py](../app/privacy/recognizers.py): three new recognizers —
  street address, Dutch phone formats, the name under a closing formula — and a BSN
  recognizer that accepts `1234.56.789`.
- [app/privacy/hints.py](../app/privacy/hints.py): `known_values_for(citizen)` turns the
  sender's citizen record into values that are redacted wherever they occur.
- [app/privacy/redaction.py](../app/privacy/redaction.py): `detect()` now combines three
  sources (known values, recognizers, propagation) and **merges** overlapping detections.
- [tests/pii_testset/](../tests/pii_testset/): the labelled test set and the recall gate.

**Three ways a recognizer decides**
- *Pattern + checksum* (BSN, IBAN): the shape narrows it down, the checksum confirms it.
  Near-certain.
- *Pattern + context word*: "Kerkbuurt 12" has the same shape as "Artikel 12". The loose
  pattern scores 0.2, below the 0.4 threshold; a word like "adres" or "woon" nearby adds
  to the score and lifts it over.
- *Position*: the line under "Met vriendelijke groet," is a name, whatever it says. NER
  fails here because a lone name has no sentence around it.

**Decisions and why**
- **Merge, do not choose.** When detections overlapped, the most confident used to win.
  NER found "Kerkstraat" (0.85) and beat the pattern that had found "Kerkstraat 12"
  (0.75), so the house number stayed visible. Now overlapping detections become one span
  covering all of them. This single change took address recall from 81% to 100%.
- **Known values.** The municipality already knows who the sender is. Their name, BSN,
  address, phone and email are redacted by exact match, case-insensitively, also as
  first name or family name alone. Certain, where recognizers only guess.
- **Two gates, not one.** Known values only help for the sender. A partner or neighbour
  mentioned in the email is protected by the recognizers alone, so that group is
  measured and gated separately.
- Strict counting: a value counts as found only if it is redacted completely.

**Seen in practice** (330 emails, 1,260 labelled values)

| | Stage 2 baseline | Stage 3 |
| --- | --- | --- |
| Street addresses | 5 of 94 | 210 of 210 |
| Phone numbers | 47 of 64 | 150 of 150 |
| Names, recognizers alone | 121 of 168 | 416 of 435 |
| Whole pipeline | — | 99.8% |
| Other people's values | — | 98.6% |

- Remaining misses are all names: lowercase names without a closing formula, and a
  family name that is also a verb ("Moet").
- Presidio looks up its own recognizers by class name: a class called `PhoneRecognizer`
  collided with the built-in one. Ours are named `Dutch...`.
- **These numbers flatter.** The emails come from 22 templates written by the same hand
  that tuned the recognizers. Real mail would score lower.

**Try it**
- `uv run pytest tests/pii_testset -s` prints both tables and every miss.
- Add a template to [hard_cases.py](../tests/pii_testset/hard_cases.py) that you think
  will slip through, run the gate, and see whether it does.

**Still open**
- Cities are redacted only when NER spots them, and are not labelled in the test set.
- Precision is tested with a handful of sentences, not measured.

### Stage 4 — 2026-10-11 (a surname left standing)

**What was found**
- In a case note, "Sanne Voorbeeld" became "[PERSON_1] Voorbeeld". NER recognised the
  first name and read the surname as the ordinary word it also is.
- The gate already showed the same pattern with a generated name ("Bjorn Moet").

**Case first, then fix**
- New slot `word_name` in the generator (a first name plus a surname from
  `WORD_SURNAMES`: Bos, Vos, Kok, Prins, Koning, …), a template in
  [hard_cases.py](../tests/pii_testset/hard_cases.py), and a test per surname in
  [test_redaction.py](../tests/unit/test_redaction.py). The test for "Voorbeeld" failed.
- Fix in [redaction.py](../app/privacy/redaction.py), `_with_surname`: a one-word name is
  extended over the capitalised word that follows it, with prefixes such as "van de" in
  between. Not across a line break, and not over an abbreviation such as "BSN".

**Seen in practice**
- Real word-surnames (Bos, Koning, Timmerman) were already caught; only the made-up one
  was not. The fix matters for surnames the model has never seen.
- Recall for other people: 98.7% → 99.1%. Recognizers alone: 98.5% → 98.8%.
- Still missed: "Aylin van de Berg". There the *first* name is not recognised, so there
  is nothing to extend from.
- The trade-off is deliberate: "Hallo Sanne Kunt u ..." without a comma now loses the
  word "Kunt". Losing a word is the cheaper mistake.
- NER also over-redacts on its own: "Sanne van de gemeente" becomes one PERSON.

### Stage 4 — 2026-10-11 (a town went to the cloud)

**What was found**
- The first real cloud run sent "[ADDRESS_1], [POSTCODE_1] Spankeren" to the model.
  Street and postcode were replaced; the village was not.
- Towns were not labelled in the test set, so the recall gate could not see it. Once
  labelled, ADDRESS recall was 81.5%: NER knows Utrecht, not Spankeren. About half of
  the town names were left standing.

**Case first, then fix**
- `city` is now a labelled slot, plus a hard case with a second person's town.
- Two fixes: the sender's town is taken from their citizen record
  ([hints.py](../app/privacy/hints.py)), and whatever capitalised word or words follow a
  postcode are treated as the town ([redaction.py](../app/privacy/redaction.py),
  `_town_after`). In an address, the position says what the word is.
- Once a town is recognised behind a postcode, the existing propagation step redacts it
  everywhere else in the email.

**Seen in practice**
- ADDRESS recall: 81.5% → 99.7% for the pipeline, 96.8% for the recognizers alone.
- What is still missed: a second person's village mentioned only in running text, with
  no postcode near it ("mijn vader woont in Luxwoude").
- The lesson is about the gate, not the recognizer: **what is not labelled is not
  measured.** The gate said 99.8% the whole time.

**Still open**
- ~~A list of all Dutch place names would close the remaining gap.~~ Added the same day:
  the 2,427 official place names from CBS open data
  ([woonplaatsen.txt](../app/privacy/data/woonplaatsen.txt), refreshed with
  `scripts/fetch_place_names.py`), as `DutchPlaceNameRecognizer`.
- A name from the list counts only after a preposition ("in", "te", "naar", "uit", …).
  "Best", "Made", "Een" and "Hee" are villages and ordinary words; "in Best" is a place,
  "Best vervelend" is not.
- ADDRESS recall is now 100% on the test set. Read that number with care: the test
  towns are generated from a list that overlaps the official one almost completely, and
  always appear after a preposition or a postcode. A village written as "Luxwoude is
  mijn dorp" is still missed.
- The list came from CBS on purpose, not from the generator's own list, so the
  recognizer is not built from the test data.
