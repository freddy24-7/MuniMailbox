import hashlib
from dataclasses import dataclass
from typing import NewType

# Text from the knowledge base: published by the municipality, about nobody in
# particular. The inference router sends it to a model without the redaction check.
PublicText = NewType("PublicText", str)


@dataclass(frozen=True)
class Document:
    url: str
    title: str
    topic: str
    fetched_at: str  # ISO date; shown with every citation so staleness is visible
    text: str


@dataclass(frozen=True)
class Chunk:
    id: str
    text: str
    title: str
    heading: str
    url: str
    topic: str
    fetched_at: str


def _sections(text: str) -> list[tuple[str, list[str]]]:
    """Split on heading lines: [(heading, paragraphs), ...]."""
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in text.splitlines():
        if line.startswith("#"):
            sections.append((line.lstrip("# ").strip(), []))
        elif line.strip():
            sections[-1][1].append(line.strip())
    return [(heading, paragraphs) for heading, paragraphs in sections if paragraphs]


def chunk_document(document: Document, *, max_chars: int) -> list[Chunk]:
    """One chunk per section; a long section is split between paragraphs.

    Every chunk starts with the page title and its heading. A passage such as "Dit kost
    € 25" means nothing on its own; with "Grofvuil - Kosten" in front it can be found.
    When a section is split, the last paragraph is repeated at the start of the next
    chunk, so a sentence that refers back to the one before keeps its context.
    """
    chunks: list[Chunk] = []
    for heading, paragraphs in _sections(document.text):
        label = (
            f"{document.title} - {heading}"
            if heading and heading != document.title
            else (document.title)
        )
        bodies: list[list[str]] = [[]]
        for paragraph in paragraphs:
            current = bodies[-1]
            if current and sum(len(p) + 1 for p in current) + len(paragraph) > max_chars:
                bodies.append([current[-1]] if len(current[-1]) < max_chars // 4 else [])
            bodies[-1].append(paragraph)
        for body in bodies:
            identity = f"{document.url}#{len(chunks)}"
            chunks.append(
                Chunk(
                    id=hashlib.sha256(identity.encode()).hexdigest()[:16],
                    text=f"{label}\n" + "\n".join(body),
                    title=document.title,
                    heading=heading,
                    url=document.url,
                    topic=document.topic,
                    fetched_at=document.fetched_at,
                )
            )
    return chunks
