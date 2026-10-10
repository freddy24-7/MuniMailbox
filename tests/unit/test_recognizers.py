import pytest

from app.privacy.entities import EntityType
from app.privacy.redaction import Redactor
from tests.helpers import BSN


def _types(redactor: Redactor, text: str) -> list[EntityType]:
    return [entry.entity_type for entry in redactor.redact([text]).mapping]


@pytest.mark.parametrize(
    "phone",
    [
        "06-12345678",
        "06 12345678",
        "0612345678",
        "020-1234567",
        "(020) 1234567",
        "(0512) 123456",
        "+31612345678",
        "+31 6 12345678",
        "+31(0)20-1234567",
        "0031 6 12345678",
    ],
)
def test_dutch_phone_formats(redactor: Redactor, phone: str) -> None:
    (redacted,) = redactor.redact([f"U kunt mij bellen op {phone} na vijf uur."]).texts
    assert redacted == "U kunt mij bellen op [PHONE_1] na vijf uur."


@pytest.mark.parametrize(
    "address",
    ["Kerkstraat 12", "Lange Nieuwstraat 4a", "Prinses Beatrixlaan 101-II", "Dorpsweg 7 bis"],
)
def test_street_addresses_with_a_typical_ending(redactor: Redactor, address: str) -> None:
    (redacted,) = redactor.redact([f"De lantaarnpaal voor {address} is kapot."]).texts
    assert redacted == "De lantaarnpaal voor [ADDRESS_1] is kapot."


def test_street_without_a_typical_ending_needs_a_context_word(redactor: Redactor) -> None:
    (with_context,) = redactor.redact(["Mijn adres is Achterom 14."]).texts
    assert with_context == "Mijn adres is [ADDRESS_1]."


def test_bsn_written_in_groups(redactor: Redactor) -> None:
    dotted = f"{BSN[:4]}.{BSN[4:6]}.{BSN[6:]}"
    (redacted,) = redactor.redact([f"Mijn burgerservicenummer is {dotted}."]).texts
    assert redacted == "Mijn burgerservicenummer is [BSN_1]."


def test_the_line_under_a_closing_formula_is_a_name(redactor: Redactor) -> None:
    # Not a name any model would know; found by position alone.
    (redacted,) = redactor.redact(
        ["Graag een reactie.\n\nMet vriendelijke groet,\nXyq Zzyzx"]
    ).texts
    assert redacted.endswith("Met vriendelijke groet,\n[PERSON_1]")


@pytest.mark.parametrize(
    "text",
    [
        "Volgens Artikel 12 van de verordening mag dat.",
        "Ik heb in 2024 in de gemeente gewoond.",
        "Over zaak 200400600 heb ik nog niets gehoord.",
        "Is het gemeentehuis op zaterdag geopend?",
        "De afspraak is volgende week dinsdag om 10 uur.",
    ],
)
def test_ordinary_text_is_left_alone(redactor: Redactor, text: str) -> None:
    """Precision: a redactor that replaces everything has perfect recall and is useless."""
    assert redactor.redact([text]).texts == [text]
    assert _types(redactor, text) == []
