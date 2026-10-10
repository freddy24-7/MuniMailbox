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
class MappingEntry:
    placeholder: str
    entity_type: EntityType
    value: str


@dataclass(frozen=True)
class RedactionResult:
    texts: list[RedactedText]
    mapping: list[MappingEntry]


@dataclass(frozen=True)
class _Span:
    start: int
    end: int
    entity_type: EntityType
    score: float

    def overlaps(self, other: "_Span") -> bool:
        return self.start < other.end and other.start < self.end


def _without_overlaps(spans: list[_Span]) -> list[_Span]:
    """Where detections overlap, keep the most confident, then the longest."""
    kept: list[_Span] = []
    for span in sorted(spans, key=lambda s: (-s.score, -(s.end - s.start), s.start)):
        if not any(span.overlaps(other) for other in kept):
            kept.append(span)
    return kept


class Redactor:
    def __init__(self, analyzer: AnalyzerEngine, score_threshold: float) -> None:
        self._analyzer = analyzer
        self._score_threshold = score_threshold

    def _detect(self, text: str) -> list[_Span]:
        results = self._analyzer.analyze(
            text,
            language=LANGUAGE,
            entities=list(ENTITY_MAP),
            score_threshold=self._score_threshold,
        )
        return _without_overlaps(
            [_Span(r.start, r.end, ENTITY_MAP[r.entity_type], r.score) for r in results]
        )

    def redact(self, texts: Sequence[str]) -> RedactionResult:
        """Redact several fields of one email with one shared mapping, so `[PERSON_1]`
        means the same person in the subject and in the body.

        Synchronous and CPU-bound: call it through `asyncio.to_thread`.
        """
        spans_per_text = [self._detect(text) for text in texts]

        # A value detected once is redacted everywhere it occurs. NER depends on context
        # and often recognises a name in the signature but not in the opening line.
        known: dict[str, EntityType] = {}
        for text, spans in zip(texts, spans_per_text, strict=True):
            for span in spans:
                known.setdefault(text[span.start : span.end], span.entity_type)
        for text, spans in zip(texts, spans_per_text, strict=True):
            for value, entity_type in known.items():
                for match in re.finditer(rf"(?<!\w){re.escape(value)}(?!\w)", text):
                    candidate = _Span(match.start(), match.end(), entity_type, 1.0)
                    if not any(candidate.overlaps(other) for other in spans):
                        spans.append(candidate)

        placeholders: dict[tuple[EntityType, str], str] = {}
        counters: dict[EntityType, int] = {}
        redacted: list[RedactedText] = []
        for text, spans in zip(texts, spans_per_text, strict=True):
            parts: list[str] = []
            position = 0
            for span in sorted(spans, key=lambda s: s.start):
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
