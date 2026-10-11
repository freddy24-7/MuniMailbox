"""Keyword search with BM25 (FR-16), in plain Python.

A chunk scores high when it contains the query's words often, counting rare words more
than common ones and long chunks less than short ones. No model involved: it finds exact
terms ("individuele inkomenstoeslag", "kliko") that an embedding may blur.
"""

import math
import re
from collections import Counter

# Words too common to tell chunks apart.
STOPWORDS = frozenset(
    "aan al als bij dan dat de den der des deze die dit door een en er had heb hebben heeft "
    "het hij hoe ik in is je kan kunnen maar me met mij mijn naar niet nog nu of om omdat "
    "ons ook op over te tot u uit uw van veel voor waar was wat we wel werd wie wij wil "
    "worden wordt zal ze zich zij zijn zo zou geachte beste groet groeten vriendelijke "
    "heer mevrouw graag kunt".split()
)
_TOKEN = re.compile(r"[a-zà-ÿ0-9]+")


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if len(t) > 1 and t not in STOPWORDS]


class Bm25Index:
    def __init__(self, documents: dict[str, str], *, k1: float = 1.5, b: float = 0.75) -> None:
        self._k1, self._b = k1, b
        self._term_counts = {doc_id: Counter(tokenize(text)) for doc_id, text in documents.items()}
        self._lengths = {
            doc_id: sum(counts.values()) for doc_id, counts in self._term_counts.items()
        }
        self._average_length = sum(self._lengths.values()) / max(len(self._lengths), 1)
        document_frequency: Counter[str] = Counter()
        for counts in self._term_counts.values():
            document_frequency.update(counts.keys())
        total = len(documents)
        self._idf = {
            term: math.log(1 + (total - frequency + 0.5) / (frequency + 0.5))
            for term, frequency in document_frequency.items()
        }

    def search(self, query: str, limit: int) -> list[tuple[str, float]]:
        """The best-matching document ids with their scores, best first. Documents that
        share no word with the query are not returned."""
        terms = [term for term in set(tokenize(query)) if term in self._idf]
        scores: dict[str, float] = {}
        for doc_id, counts in self._term_counts.items():
            relative_length = self._lengths[doc_id] / (self._average_length or 1)
            length_norm = 1 - self._b + self._b * relative_length
            score = 0.0
            for term in terms:
                count = counts.get(term, 0)
                if count:
                    saturation = count * (self._k1 + 1) / (count + self._k1 * length_norm)
                    score += self._idf[term] * saturation
            if score > 0:
                scores[doc_id] = score
        return sorted(scores.items(), key=lambda item: -item[1])[:limit]
