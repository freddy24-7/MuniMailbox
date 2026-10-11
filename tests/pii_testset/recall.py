"""Builds the labelled test set and measures how much of it the redactor finds (GR-4)."""

import random
from collections import Counter
from dataclasses import dataclass, field

from app.db.models import Citizen
from app.privacy.entities import EntityType
from app.privacy.hints import known_values_for
from app.privacy.redaction import Redactor
from scripts.generate_synthetic import (
    TEMPLATES,
    PiiSpan,
    SyntheticCitizen,
    SyntheticEmail,
    generate_citizens,
    name_with_word_surname,
    render_email,
)
from tests.pii_testset.hard_cases import HARD_CASES

SEED = 20261010
CITIZENS = 60
EMAILS_PER_TEMPLATE = 15


@dataclass(frozen=True)
class Case:
    email: SyntheticEmail
    sender: SyntheticCitizen
    other: SyntheticCitizen

    def about_other(self, span: PiiSpan) -> bool:
        """True for a value the sender's citizen record cannot help with."""
        other_values = {self.other.name, self.other.street_address, self.other.postcode}
        other_values |= {self.other.bsn, self.other.iban, self.other.phone, self.other.email}
        other_values |= {self.other.date_of_birth, name_with_word_surname(self.other.name)}
        return span.value in other_values


def build_cases() -> list[Case]:
    rng = random.Random(SEED)
    people = generate_citizens(CITIZENS, seed=SEED)
    cases = []
    for template in (*TEMPLATES, *HARD_CASES):
        for _ in range(EMAILS_PER_TEMPLATE):
            sender, other = rng.sample(people, 2)
            cases.append(Case(render_email(template, sender, rng, other), sender, other))
    return cases


@dataclass
class Report:
    found: Counter[str] = field(default_factory=Counter)
    total: Counter[str] = field(default_factory=Counter)
    # (entity type, the labelled value, what was redacted around it, the subject)
    misses: list[tuple[EntityType, str, str]] = field(default_factory=list)

    def recall(self, key: str = "ALL") -> float:
        return self.found[key] / self.total[key] if self.total[key] else 1.0

    def add(self, keys: list[str], hit: bool) -> None:
        for key in keys:
            self.total[key] += 1
            self.found[key] += hit

    def table(self) -> str:
        rows = [f"{'':24}{'found':>7}{'total':>7}{'recall':>9}"]
        for key in sorted(self.total, key=lambda k: (k != "ALL", k)):
            rows.append(f"{key:24}{self.found[key]:>7}{self.total[key]:>7}{self.recall(key):>9.1%}")
        return "\n".join(rows)


def measure(redactor: Redactor, cases: list[Case], *, use_citizen_record: bool) -> Report:
    """A labelled value counts as found only if it is redacted completely."""
    report = Report()
    for case in cases:
        sender = case.sender
        known = (
            known_values_for(
                Citizen(
                    name=sender.name,
                    address=sender.address,
                    bsn=sender.bsn,
                    email=sender.email,
                    phone=sender.phone,
                )
            )
            if use_citizen_record
            else []
        )
        subject_spans, body_spans = redactor.detect([case.email.subject, case.email.body], known)
        for labelled in case.email.pii:
            detected = subject_spans if labelled.field == "subject" else body_spans
            hit = any(d.start <= labelled.start and labelled.end <= d.end for d in detected)
            keys = ["ALL", labelled.entity_type.value]
            keys.append("other people" if case.about_other(labelled) else "the sender")
            report.add(keys, hit)
            if not hit:
                report.misses.append((labelled.entity_type, labelled.value, case.email.subject))
    return report
