from enum import StrEnum


class EntityType(StrEnum):
    """PII entity types (FR-5). The value is the prefix of the placeholder: `[PERSON_1]`."""

    PERSON = "PERSON"
    ADDRESS = "ADDRESS"
    POSTCODE = "POSTCODE"
    BSN = "BSN"
    IBAN = "IBAN"
    PHONE = "PHONE"
    EMAIL = "EMAIL"
    DATE_OF_BIRTH = "DATE_OF_BIRTH"
