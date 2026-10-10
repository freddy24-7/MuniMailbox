import re

from app.db.models import Citizen
from app.privacy.entities import EntityType
from app.privacy.redaction import KnownValue

_POSTCODE = re.compile(r"\b[1-9]\d{3} ?[A-Za-z]{2}\b")


def known_values_for(citizen: Citizen) -> list[KnownValue]:
    """What the municipality already has on file about the sender.

    Recognizers guess; these values are certain. They cover the sender's own data even
    when it is written in a way no recognizer expects. Other people mentioned in the
    email are not covered: for them the recognizers are the only protection.
    """
    known = [
        KnownValue(EntityType.PERSON, citizen.name),
        KnownValue(EntityType.BSN, citizen.bsn),
        KnownValue(EntityType.EMAIL, citizen.email),
        KnownValue(EntityType.PHONE, citizen.phone),
    ]
    # "Lola Spaan" also signs as "Lola" or is addressed as "mevrouw Spaan".
    first_name, _, family_name = citizen.name.partition(" ")
    known += [
        KnownValue(EntityType.PERSON, part) for part in (first_name, family_name) if len(part) > 2
    ]
    street, _, _ = citizen.address.partition(",")
    if street.strip():
        known.append(KnownValue(EntityType.ADDRESS, street.strip()))
    if postcode := _POSTCODE.search(citizen.address):
        known.append(KnownValue(EntityType.POSTCODE, postcode.group()))
    return [value for value in known if value.value]
