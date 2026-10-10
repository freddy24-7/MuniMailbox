"""Custom Dutch recognizers (GR-1). The remaining ones (phone formats, street addresses)
are added together with the PII test set."""

from presidio_analyzer import Pattern, PatternRecognizer

from app.privacy.checksums import bsn_is_valid

LANGUAGE = "nl"


class BsnRecognizer(PatternRecognizer):
    """Nine digits are only a BSN if they pass the 11-check."""

    def __init__(self) -> None:
        super().__init__(
            supported_entity="NL_BSN",
            supported_language=LANGUAGE,
            patterns=[Pattern("bsn", r"\b\d{9}\b", 0.3)],
            context=["bsn", "burgerservicenummer", "sofinummer"],
        )

    def validate_result(self, pattern_text: str) -> bool:
        # True lifts the score to 1.0; False discards the match.
        return bsn_is_valid(pattern_text)


def postcode_recognizer() -> PatternRecognizer:
    return PatternRecognizer(
        supported_entity="NL_POSTCODE",
        supported_language=LANGUAGE,
        patterns=[Pattern("postcode", r"\b[1-9]\d{3} ?[A-Z]{2}\b", 0.7)],
        context=["postcode", "adres"],
        global_regex_flags=0,  # case-sensitive: "1234 ab" in running text is rarely a postcode
    )


def numeric_date_recognizer() -> PatternRecognizer:
    """Dates written with digits (01-02-1980). Relative dates ("volgende week") are left
    alone: they identify nobody and the draft needs them."""
    return PatternRecognizer(
        supported_entity="NL_DATE",
        supported_language=LANGUAGE,
        patterns=[Pattern("numeric_date", r"\b\d{1,2}[-/.]\d{1,2}[-/.](?:19|20)\d{2}\b", 0.7)],
        context=["geboren", "geboortedatum"],
    )
