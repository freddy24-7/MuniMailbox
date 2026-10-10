"""Generate synthetic citizens, cases, demo users and a labelled email feed (GR-9).

Citizens, cases and users are written to the database (the mock case-management system).
Emails are written to a JSONL feed that the intake step reads; each email carries its
ground truth: topic, urgency, sensitivity and the exact position of every PII value.

    uv run python scripts/generate_synthetic.py --citizens 200 --emails 500
"""

import argparse
import asyncio
import json
import random
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from string import Formatter

from faker import Faker
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Case, CaseStatus, Citizen, Role, Topic, Urgency, User
from app.db.session import create_engine, create_session_factory
from app.privacy.entities import EntityType

DEFAULT_FEED_PATH = Path("data/synthetic/emails.jsonl")
# Fixed "now", so a seed gives the same dataset on any day.
REFERENCE_NOW = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)

# Template slots that are personal data, and the entity type they are labelled with.
SLOT_ENTITY: dict[str, EntityType] = {
    "name": EntityType.PERSON,
    "street_address": EntityType.ADDRESS,
    "postcode": EntityType.POSTCODE,
    "bsn": EntityType.BSN,
    "iban": EntityType.IBAN,
    "phone": EntityType.PHONE,
    "email": EntityType.EMAIL,
    "date_of_birth": EntityType.DATE_OF_BIRTH,
}


@dataclass(frozen=True)
class SyntheticCitizen:
    id: uuid.UUID
    name: str
    street_address: str
    postcode: str
    city: str
    bsn: str
    email: str
    phone: str
    date_of_birth: str
    iban: str

    @property
    def address(self) -> str:
        return f"{self.street_address}, {self.postcode} {self.city}"


@dataclass(frozen=True)
class SyntheticCase:
    id: uuid.UUID
    citizen_id: uuid.UUID
    topic: Topic
    status: CaseStatus
    opened_at: datetime
    summary: str


@dataclass(frozen=True)
class SyntheticUser:
    id: uuid.UUID
    name: str
    role: Role
    team: str | None


@dataclass(frozen=True)
class PiiSpan:
    field: str  # "subject" or "body"
    start: int
    end: int
    entity_type: EntityType
    value: str


@dataclass(frozen=True)
class SyntheticEmail:
    id: uuid.UUID
    citizen_id: uuid.UUID
    sender: str
    subject: str
    body: str
    received_at: datetime
    attachments: list[str]
    topic: Topic
    urgency: Urgency
    sensitive: bool
    pii: list[PiiSpan]


@dataclass(frozen=True)
class Dataset:
    citizens: list[SyntheticCitizen]
    cases: list[SyntheticCase]
    users: list[SyntheticUser]
    emails: list[SyntheticEmail]


@dataclass(frozen=True)
class EmailTemplate:
    topic: Topic
    urgency: Urgency
    subject: str
    body: str
    sensitive: bool = False
    attachments: tuple[str, ...] = ()


TEMPLATES: tuple[EmailTemplate, ...] = (
    EmailTemplate(
        Topic.PERMITS,
        Urgency.NORMAL,
        "Vraag over omgevingsvergunning dakkapel",
        "Geachte heer/mevrouw,\n\n"
        "Ik wil een dakkapel plaatsen aan de voorkant van mijn woning aan de "
        "{street_address}, {postcode} {city}. Heb ik daarvoor een omgevingsvergunning nodig, "
        "en hoe lang duurt de behandeling van een aanvraag?\n\n"
        "U kunt mij bereiken op {phone}.\n\n"
        "Met vriendelijke groet,\n{name}",
        attachments=("bouwtekening.pdf",),
    ),
    EmailTemplate(
        Topic.PERMITS,
        Urgency.LOW,
        "Parkeervergunning aanvragen",
        "Beste gemeente,\n\n"
        "Sinds kort woon ik op {street_address} ({postcode}) en ik wil graag een "
        "parkeervergunning voor bewoners aanvragen. Welke gegevens heeft u van mij nodig? "
        "Mijn BSN is {bsn}.\n\n"
        "Groet,\n{name}\n{email}",
    ),
    EmailTemplate(
        Topic.WASTE_COLLECTION,
        Urgency.NORMAL,
        "Container niet geleegd",
        "Hallo,\n\n"
        "Afgelopen week is de grijze container bij ons niet geleegd. Het gaat om "
        "{street_address} in {city}, postcode {postcode}. Wanneer wordt dit alsnog gedaan?\n\n"
        "Met vriendelijke groet,\n{name}\nTel. {phone}",
    ),
    EmailTemplate(
        Topic.WASTE_COLLECTION,
        Urgency.LOW,
        "Grofvuil laten ophalen",
        "Geachte gemeente,\n\n"
        "Ik wil een afspraak maken om grofvuil te laten ophalen: een bank en twee kasten. "
        "Mijn adres is {street_address}, {postcode} {city}. Zijn hier kosten aan verbonden?\n\n"
        "Vriendelijke groet,\n{name}",
    ),
    EmailTemplate(
        Topic.BENEFITS,
        Urgency.NORMAL,
        "Aanvraag bijzondere bijstand",
        "Geachte heer/mevrouw,\n\n"
        "Graag wil ik bijzondere bijstand aanvragen voor de kosten van een nieuwe wasmachine. "
        "Mijn gegevens: {name}, geboren op {date_of_birth}, BSN {bsn}. "
        "Een eventuele vergoeding kan worden overgemaakt op rekening {iban}.\n\n"
        "Kunt u mij laten weten welke bewijsstukken ik moet aanleveren?\n\n"
        "Met vriendelijke groet,\n{name}",
        attachments=("offerte.pdf",),
    ),
    EmailTemplate(
        Topic.BENEFITS,
        Urgency.HIGH,
        "Uitkering niet ontvangen, weet niet meer wat ik moet doen",
        "Beste mensen,\n\n"
        "Mijn uitkering is deze maand niet gestort op {iban} en ik kan de huur niet betalen. "
        "Ik slaap er niet meer van en zie het echt niet meer zitten. "
        "Mijn BSN is {bsn}. Belt u mij alstublieft zo snel mogelijk op {phone}.\n\n"
        "{name}",
        sensitive=True,
    ),
    EmailTemplate(
        Topic.COMPLAINTS,
        Urgency.NORMAL,
        "Klacht over afhandeling melding",
        "Geachte heer/mevrouw,\n\n"
        "Drie weken geleden heb ik een melding gedaan over een losliggende stoeptegel voor "
        "{street_address}. Ik heb daarna niets meer gehoord en de tegel ligt er nog steeds. "
        "Ik dien hierbij een klacht in over de afhandeling.\n\n"
        "U kunt reageren via {email} of {phone}.\n\n"
        "Hoogachtend,\n{name}",
    ),
    EmailTemplate(
        Topic.COMPLAINTS,
        Urgency.HIGH,
        "Dit pik ik niet langer",
        "Gemeente,\n\n"
        "Voor de derde keer is mijn bezwaar afgewezen zonder fatsoenlijke uitleg. "
        "Als dit niet binnen een week is opgelost, kom ik naar het gemeentehuis en dan "
        "zullen jullie het wel merken. Ik sta nergens meer voor in.\n\n"
        "{name}\n{street_address}, {postcode} {city}",
        sensitive=True,
    ),
    EmailTemplate(
        Topic.ADDRESS_CHANGE,
        Urgency.NORMAL,
        "Verhuizing doorgeven",
        "Geachte heer/mevrouw,\n\n"
        "Per de eerste van volgende maand verhuis ik naar {street_address}, {postcode} "
        "{city}. Kan ik deze verhuizing per e-mail doorgeven? "
        "Mijn gegevens zijn: {name}, geboortedatum {date_of_birth}, BSN {bsn}.\n\n"
        "Met vriendelijke groet,\n{name}",
    ),
    EmailTemplate(
        Topic.ADDRESS_CHANGE,
        Urgency.LOW,
        "Adreswijziging nog niet verwerkt?",
        "Hallo,\n\n"
        "Twee weken geleden heb ik mijn nieuwe adres doorgegeven ({street_address}, "
        "{postcode}), maar post van de gemeente komt nog op mijn oude adres aan. "
        "Kunt u nakijken of de wijziging is verwerkt? Mijn telefoonnummer is {phone}.\n\n"
        "Groeten,\n{name}",
    ),
    EmailTemplate(
        Topic.OTHER,
        Urgency.LOW,
        "Openingstijden gemeentehuis",
        "Goedemiddag,\n\n"
        "Is het gemeentehuis op zaterdag geopend, en moet ik een afspraak maken om een "
        "uittreksel op te halen?\n\n"
        "Groet,\n{name}",
    ),
    EmailTemplate(
        Topic.OTHER,
        Urgency.NORMAL,
        "Trouwlocatie reserveren",
        "Geachte gemeente,\n\n"
        "Mijn partner en ik willen volgend voorjaar trouwen. Welke trouwlocaties zijn er "
        "in {city} en hoe reserveren wij een datum? U kunt mij mailen op {email}.\n\n"
        "Met vriendelijke groet,\n{name}",
    ),
)

CASE_SUMMARIES: dict[Topic, tuple[str, ...]] = {
    Topic.PERMITS: (
        "Aanvraag omgevingsvergunning voor een aanbouw.",
        "Aanvraag parkeervergunning voor bewoners.",
    ),
    Topic.WASTE_COLLECTION: (
        "Melding: container niet geleegd.",
        "Aanvraag extra papiercontainer.",
    ),
    Topic.BENEFITS: (
        "Aanvraag bijzondere bijstand.",
        "Heronderzoek recht op uitkering.",
    ),
    Topic.COMPLAINTS: (
        "Klacht over de afhandeling van een melding openbare ruimte.",
        "Klacht over bejegening aan de balie.",
    ),
    Topic.ADDRESS_CHANGE: (
        "Verhuizing binnen de gemeente doorgegeven.",
        "Vestiging vanuit een andere gemeente.",
    ),
}

DEMO_TEAMS = ("Vergunningen en Wonen", "Werk en Inkomen")


def render(template: str, values: dict[str, str], field: str) -> tuple[str, list[PiiSpan]]:
    """Fill a template and record where each personal value ended up."""
    parts: list[str] = []
    spans: list[PiiSpan] = []
    position = 0
    for literal, slot, _, _ in Formatter().parse(template):
        parts.append(literal)
        position += len(literal)
        if slot is None:
            continue
        value = values[slot]
        if slot in SLOT_ENTITY:
            spans.append(PiiSpan(field, position, position + len(value), SLOT_ENTITY[slot], value))
        parts.append(value)
        position += len(value)
    return "".join(parts), spans


def _uuid(rng: random.Random) -> uuid.UUID:
    return uuid.UUID(int=rng.getrandbits(128), version=4)


def _citizen(fake: Faker, rng: random.Random) -> SyntheticCitizen:
    born = date(rng.randint(1936, 2006), rng.randint(1, 12), rng.randint(1, 28))
    return SyntheticCitizen(
        id=_uuid(rng),
        name=fake.name(),
        street_address=fake.street_address(),
        postcode=fake.postcode(),
        city=fake.city(),
        bsn=fake.unique.ssn(),
        # safe_email only uses reserved example.* domains, so it can never be a real mailbox.
        email=fake.unique.safe_email(),
        phone=fake.phone_number(),
        date_of_birth=born.strftime("%d-%m-%Y"),
        iban=fake.iban(),
    )


def _cases(citizen: SyntheticCitizen, rng: random.Random) -> list[SyntheticCase]:
    cases = []
    for _ in range(rng.choice((0, 0, 1, 1, 2, 3))):
        topic = rng.choice(tuple(CASE_SUMMARIES))
        cases.append(
            SyntheticCase(
                id=_uuid(rng),
                citizen_id=citizen.id,
                topic=topic,
                status=rng.choice(tuple(CaseStatus)),
                opened_at=REFERENCE_NOW - timedelta(days=rng.randint(1, 720)),
                summary=rng.choice(CASE_SUMMARIES[topic]),
            )
        )
    return cases


def _users(fake: Faker, rng: random.Random) -> list[SyntheticUser]:
    users = []
    for team in DEMO_TEAMS:
        users.append(SyntheticUser(_uuid(rng), fake.name(), Role.TEAM_LEAD, team))
        users.extend(
            SyntheticUser(_uuid(rng), fake.name(), Role.CASEWORKER, team) for _ in range(2)
        )
    users.append(SyntheticUser(_uuid(rng), fake.name(), Role.AUDITOR, None))
    return users


def _email(citizen: SyntheticCitizen, rng: random.Random) -> SyntheticEmail:
    template = rng.choice(TEMPLATES)
    values = {key: str(value) for key, value in asdict(citizen).items()}
    subject, subject_spans = render(template.subject, values, "subject")
    body, body_spans = render(template.body, values, "body")
    return SyntheticEmail(
        id=_uuid(rng),
        citizen_id=citizen.id,
        sender=citizen.email,
        subject=subject,
        body=body,
        received_at=REFERENCE_NOW - timedelta(minutes=rng.randint(0, 14 * 24 * 60)),
        attachments=list(template.attachments),
        topic=template.topic,
        urgency=template.urgency,
        sensitive=template.sensitive,
        pii=subject_spans + body_spans,
    )


def generate(citizens: int, emails: int, seed: int | None = None) -> Dataset:
    rng = random.Random(seed)
    fake = Faker("nl_NL")
    fake.seed_instance(rng.getrandbits(64))

    people = [_citizen(fake, rng) for _ in range(citizens)]
    return Dataset(
        citizens=people,
        cases=[case for person in people for case in _cases(person, rng)],
        users=_users(fake, rng),
        emails=[_email(rng.choice(people), rng) for _ in range(emails)],
    )


def write_feed(emails: list[SyntheticEmail], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as feed:
        for email in emails:
            record = {
                "id": str(email.id),
                "sender": email.sender,
                "subject": email.subject,
                "body": email.body,
                "received_at": email.received_at.isoformat(),
                "attachments": email.attachments,
                "citizen_id": str(email.citizen_id),
                "labels": {
                    "topic": email.topic,
                    "urgency": email.urgency,
                    "sensitive": email.sensitive,
                    "pii": [asdict(span) for span in email.pii],
                },
            }
            feed.write(json.dumps(record, ensure_ascii=False) + "\n")


async def persist(session: AsyncSession, dataset: Dataset) -> None:
    """Store citizens and cases; demo users only if there are none yet."""
    session.add_all(
        Citizen(id=c.id, name=c.name, address=c.address, bsn=c.bsn, email=c.email, phone=c.phone)
        for c in dataset.citizens
    )
    # Citizens first, so the foreign key from cases is satisfied.
    await session.flush()
    session.add_all(Case(**asdict(case)) for case in dataset.cases)
    if await session.scalar(select(func.count()).select_from(User)) == 0:
        session.add_all(User(**asdict(user)) for user in dataset.users)
    await session.commit()


async def _main(args: argparse.Namespace) -> None:
    dataset = generate(args.citizens, args.emails, args.seed)
    engine = create_engine(get_settings().database_url.get_secret_value())
    try:
        async with create_session_factory(engine)() as session:
            await persist(session, dataset)
    finally:
        await engine.dispose()
    write_feed(dataset.emails, args.out)
    print(
        f"Stored {len(dataset.citizens)} citizens and {len(dataset.cases)} cases; "
        f"wrote {len(dataset.emails)} emails to {args.out}"
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--citizens", type=int, default=200)
    parser.add_argument("--emails", type=int, default=500)
    parser.add_argument("--seed", type=int, default=None, help="same seed, same dataset")
    parser.add_argument("--out", type=Path, default=DEFAULT_FEED_PATH)
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(_main(_parse_args()))
