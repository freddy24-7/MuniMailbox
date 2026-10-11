import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import NewType

from presidio_analyzer import AnalyzerEngine

from app.privacy.engine import ENTITY_MAP
from app.privacy.entities import EntityType
from app.privacy.recognizers import LANGUAGE

# Text in which detected personal data has been replaced by placeholders. Only this module
# and code that loads `*_redacted` columns may produce one; the inference router accepts
# nothing else, so passing a raw string to a model is a type error.
RedactedText = NewType("RedactedText", str)


@dataclass(frozen=True)
class KnownValue:
    """A value we already know is personal, for example from the sender's citizen record.
    Redacted wherever it occurs, whatever the recognizers think."""

    entity_type: EntityType
    value: str


@dataclass(frozen=True)
class MappingEntry:
    placeholder: str
    entity_type: EntityType
    value: str


@dataclass(frozen=True)
class RedactionResult:
    texts: list[RedactedText]
    mapping: list[MappingEntry]


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    entity_type: EntityType
    score: float

    def overlaps(self, other: "Span") -> bool:
        return self.start < other.end and other.start < self.end


def _without_overlaps(spans: list[Span]) -> list[Span]:
    """Merge detections that overlap into one span covering all of them.

    Nothing that any recognizer flagged is left visible: if NER finds "Kerkstraat" and a
    pattern finds "Kerkstraat 12", the result is the longer one. The merged span takes
    the type of its most confident part.
    """
    merged: list[Span] = []
    for span in sorted(spans, key=lambda s: (s.start, -s.end)):
        if merged and span.start < merged[-1].end:
            last = merged[-1]
            strongest = max(last, span, key=lambda s: (s.score, s.end - s.start))
            merged[-1] = Span(
                last.start, max(last.end, span.end), strongest.entity_type, strongest.score
            )
        else:
            merged.append(span)
    return merged


def _occurrences(value: str, text: str, *, ignore_case: bool) -> list[tuple[int, int]]:
    pattern = rf"(?<!\w){re.escape(value)}(?!\w)"
    return [m.span() for m in re.finditer(pattern, text, re.IGNORECASE if ignore_case else 0)]


class Redactor:
    def __init__(self, analyzer: AnalyzerEngine, score_threshold: float) -> None:
        self._analyzer = analyzer
        self._score_threshold = score_threshold

    def _recognise(self, text: str) -> list[Span]:
        results = self._analyzer.analyze(
            text,
            language=LANGUAGE,
            entities=list(ENTITY_MAP),
            score_threshold=self._score_threshold,
        )
        return [Span(r.start, r.end, ENTITY_MAP[r.entity_type], r.score) for r in results]

    def detect(
        self, texts: Sequence[str], known_values: Sequence[KnownValue] = ()
    ) -> list[list[Span]]:
        """Everything that will be replaced, per text, without overlaps.

        Three sources, in order of trust:
        1. known values (certain);
        2. the recognizers (scored);
        3. propagation: a value recognised once is redacted everywhere it occurs. NER
           depends on context and often finds a name in the signature but not in the
           opening line.
        """
        spans_per_text: list[list[Span]] = []
        for text in texts:
            known = [
                Span(start, end, known_value.entity_type, 1.0)
                for known_value in known_values
                for start, end in _occurrences(known_value.value, text, ignore_case=True)
            ]
            spans_per_text.append(_without_overlaps(known + self._recognise(text)))

        recognised: dict[str, EntityType] = {}
        for text, spans in zip(texts, spans_per_text, strict=True):
            for span in spans:
                recognised.setdefault(text[span.start : span.end], span.entity_type)
        for text, spans in zip(texts, spans_per_text, strict=True):
            for value, entity_type in recognised.items():
                for start, end in _occurrences(value, text, ignore_case=False):
                    candidate = Span(start, end, entity_type, 1.0)
                    if not any(candidate.overlaps(other) for other in spans):
                        spans.append(candidate)
            spans.sort(key=lambda s: s.start)
        return spans_per_text

    def redact(
        self,
        texts: Sequence[str],
        known_values: Sequence[KnownValue] = (),
        existing: Sequence[MappingEntry] = (),
    ) -> RedactionResult:
        """Redact several fields of one email with one shared mapping, so `[PERSON_1]`
        means the same person in the subject and in the body.

        The returned mapping includes the `existing` entries.

        Synchronous and CPU-bound: call it through `asyncio.to_thread`.
        """
        # `existing` continues an email's mapping for text that joins it later (case
        # history): known values keep their placeholder, new ones get the next number.
        placeholders = {(entry.entity_type, entry.value): entry.placeholder for entry in existing}
        counters: dict[EntityType, int] = {}
        for entry in existing:
            number = int(entry.placeholder.rstrip("]").rsplit("_", 1)[1])
            counters[entry.entity_type] = max(counters.get(entry.entity_type, 0), number)
        known_values = [
            *known_values,
            *(KnownValue(entry.entity_type, entry.value) for entry in existing),
        ]
        redacted: list[RedactedText] = []
        for text, spans in zip(texts, self.detect(texts, known_values), strict=True):
            parts: list[str] = []
            position = 0
            for span in spans:
                key = (span.entity_type, text[span.start : span.end])
                if key not in placeholders:
                    counters[span.entity_type] = counters.get(span.entity_type, 0) + 1
                    placeholders[key] = f"[{span.entity_type}_{counters[span.entity_type]}]"
                parts.append(text[position : span.start])
                parts.append(placeholders[key])
                position = span.end
            parts.append(text[position:])
            redacted.append(RedactedText("".join(parts)))

        return RedactionResult(
            texts=redacted,
            mapping=[
                MappingEntry(placeholder, entity_type, value)
                for (entity_type, value), placeholder in placeholders.items()
            ],
        )
