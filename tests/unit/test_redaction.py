import pytest

from app.privacy.entities import EntityType
from app.privacy.redaction import KnownValue, Redactor
from scripts.generate_synthetic import WORD_SURNAMES
from tests.helpers import BSN, IBAN, POSTCODE, SENDER


def test_structured_identifiers_are_replaced_by_typed_placeholders(redactor: Redactor) -> None:
    text = f"Mijn BSN is {BSN}, mail {SENDER}, postcode {POSTCODE}, rekening {IBAN}."
    result = redactor.redact([text])

    (redacted,) = result.texts
    for value in (BSN, SENDER, POSTCODE, IBAN):
        assert value not in redacted
    assert (
        redacted == "Mijn BSN is [BSN_1], mail [EMAIL_1], postcode [POSTCODE_1], rekening [IBAN_1]."
    )
    assert {(entry.placeholder, entry.value) for entry in result.mapping} == {
        ("[BSN_1]", BSN),
        ("[EMAIL_1]", SENDER),
        ("[POSTCODE_1]", POSTCODE),
        ("[IBAN_1]", IBAN),
    }


def test_nine_digits_that_fail_the_11_check_are_not_a_bsn(redactor: Redactor) -> None:
    not_a_bsn = BSN[:-1] + str((int(BSN[-1]) + 1) % 10)
    result = redactor.redact([f"Mijn dossiernummer is {not_a_bsn}."])
    assert not any(entry.entity_type is EntityType.BSN for entry in result.mapping)


def test_same_value_gets_the_same_placeholder_across_fields(redactor: Redactor) -> None:
    result = redactor.redact([f"Vraag van {SENDER}", f"Mail mij op {SENDER} of {SENDER}."])
    assert result.texts == ["Vraag van [EMAIL_1]", "Mail mij op [EMAIL_1] of [EMAIL_1]."]
    assert len(result.mapping) == 1


def test_different_values_of_one_type_are_numbered(redactor: Redactor) -> None:
    result = redactor.redact(["Van een@example.org naar twee@example.org."])
    assert result.texts == ["Van [EMAIL_1] naar [EMAIL_2]."]


def test_numeric_dates_are_redacted_but_relative_dates_are_kept(redactor: Redactor) -> None:
    (redacted,) = redactor.redact(["Ik ben geboren op 03-07-1984 en verhuis volgende maand."]).texts
    assert "03-07-1984" not in redacted
    assert "[DATE_OF_BIRTH_1]" in redacted
    assert "volgende maand" in redacted


def test_text_without_personal_data_is_unchanged(redactor: Redactor) -> None:
    text = "Is het gemeentehuis op zaterdag geopend?"
    result = redactor.redact([text])
    assert result.texts == [text]
    assert result.mapping == []


def test_known_values_are_redacted_however_they_are_written(redactor: Redactor) -> None:
    """The sender's own record: certain, so no recognizer has to agree."""
    known = [KnownValue(EntityType.PERSON, "Xyq Zzyzx"), KnownValue(EntityType.PERSON, "Zzyzx")]
    result = redactor.redact(["ik ben xyq zzyzx. Vraag naar mevrouw Zzyzx."], known)
    assert result.texts == ["ik ben [PERSON_1]. Vraag naar mevrouw [PERSON_2]."]


def test_overlapping_detections_are_merged_not_chosen_between(redactor: Redactor) -> None:
    """Nothing a recognizer flagged stays visible because another recognizer won."""
    known = [KnownValue(EntityType.ADDRESS, "Kerkstraat")]
    (redacted,) = redactor.redact(["Ik woon op Kerkstraat 12 in het centrum."], known).texts
    assert redacted == "Ik woon op [ADDRESS_1] in het centrum."


@pytest.mark.parametrize("surname", WORD_SURNAMES)
def test_a_surname_that_is_an_ordinary_word_goes_with_its_first_name(
    redactor: Redactor, surname: str
) -> None:
    """Found in Stage 4, in a case note: "Voorbeeld" was left standing after "[PERSON_1]"."""
    result = redactor.redact([f"Aanvraag dakkapel van Sanne {surname}, namens de buren."])
    assert result.texts == ["Aanvraag dakkapel van [PERSON_1], namens de buren."]


def test_a_surname_with_a_prefix_goes_with_its_first_name(redactor: Redactor) -> None:
    result = redactor.redact(["De aanvraag is van Sanne van de Voorbeeld, mijn buurvrouw."])
    assert result.texts == ["De aanvraag is van [PERSON_1], mijn buurvrouw."]


def test_words_after_a_first_name_that_are_not_a_surname_are_kept(redactor: Redactor) -> None:
    for text, kept in (
        ("Ik heb Sanne gisteren gesproken over de vergunning.", "gisteren"),
        ("Aanvraag van Sanne. Kunt u de ontvangst bevestigen?", "Kunt u"),
        ("Uitkering van Sanne BSN onbekend.", "BSN onbekend"),
    ):
        assert kept in redactor.redact([text]).texts[0], text
