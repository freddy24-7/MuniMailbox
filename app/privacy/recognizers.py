"""Custom Dutch recognizers (GR-1).

Pattern scores below the redaction threshold only count when a context word is near,
which Presidio adds to the score. That is how a loose pattern stays usable.
"""

import re

from presidio_analyzer import Pattern, PatternRecognizer

from app.privacy.checksums import bsn_is_valid

LANGUAGE = "nl"


class DutchBsnRecognizer(PatternRecognizer):
    """Nine digits, possibly written in groups, are only a BSN if they pass the 11-check."""

    def __init__(self) -> None:
        super().__init__(
            supported_entity="NL_BSN",
            supported_language=LANGUAGE,
            patterns=[Pattern("bsn", r"\b\d(?:[ .]?\d){8}\b", 0.3)],
            context=["bsn", "burgerservicenummer", "sofinummer"],
        )

    def validate_result(self, pattern_text: str) -> bool:
        # True lifts the score to 1.0; False discards the match.
        return bsn_is_valid(re.sub(r"[ .]", "", pattern_text))


class DutchPhoneRecognizer(PatternRecognizer):
    """(Named "Dutch..." because Presidio looks its own recognizers up by class name.)

    Dutch numbers in the ways people write them: 06-12345678, (020) 1234567,
    +31 6 12345678, +31(0)20-1234567, 0031..."""

    def __init__(self) -> None:
        separator = r"[ \-]?"
        super().__init__(
            supported_entity="NL_PHONE",
            supported_language=LANGUAGE,
            patterns=[
                Pattern(
                    "international",
                    rf"(?<![\w+])(?:\+31|0031){separator}(?:\(0\){separator})?\d(?:{separator}\d){{8,9}}(?!\d)",
                    0.4,
                ),
                Pattern(
                    "national",
                    rf"(?<![\w+(])\(?0\d{{1,3}}\)?{separator}\d(?:{separator}\d){{5,7}}(?!\d)",
                    0.4,
                ),
            ],
            context=["telefoon", "telefoonnummer", "tel", "bel", "bellen", "bereiken", "mobiel"],
        )

    def validate_result(self, pattern_text: str) -> bool | None:
        digits = re.sub(r"\D", "", pattern_text)
        if digits.startswith("0031"):
            digits = digits[4:]
        elif pattern_text.lstrip().startswith("+31"):
            digits = digits[2:]
        else:
            return len(digits) == 10
        # After the country code: nine digits, or ten when the trunk zero is written too.
        return len(digits) in (9, 10)


# Endings of Dutch street names. Not complete: streets without one are caught by the
# generic pattern below, by NER, or because the sender's own address is known.
_STREET_ENDINGS = (
    "straat|laan|weg|plein|hof|dreef|ring|steeg|pad|kade|gracht|singel|dijk|park|baan|"
    "boulevard|erf|plantsoen|markt|wal|veld|dwarsstraat|dam|haven|burgwal|vest|oord|kamp"
)
# A character inside a name: letters, digits, apostrophes (' and \u2019), dot, hyphen.
_WORD_CHAR = r"[\w'\u2019.\-]"
_HOUSE_NUMBER = r"\d{1,4}(?:[ -]?(?:[a-zA-Z]|[IVX]{1,4}|bis|hs|\d{1,2}))?(?![\w])"


def street_address_recognizer() -> PatternRecognizer:
    return PatternRecognizer(
        supported_entity="NL_STREET_ADDRESS",
        supported_language=LANGUAGE,
        patterns=[
            Pattern(
                "street_with_known_ending",
                rf"\b(?:[A-Z]{_WORD_CHAR}*[ ]){{0,3}}[A-Z]{_WORD_CHAR}*"
                rf"(?:{_STREET_ENDINGS})[ ]{_HOUSE_NUMBER}",
                0.75,
            ),
            # "Kerkbuurt 12", "Lange Voorhout 3": capitalised words and a house number.
            # Too loose alone ("Artikel 12"), so it needs a context word to pass.
            Pattern(
                "capitalised_words_and_number",
                rf"\b[A-Z]{_WORD_CHAR}+(?:[ ][A-Z]?{_WORD_CHAR}+){{0,2}}[ ]{_HOUSE_NUMBER}",
                0.2,
            ),
        ],
        context=["adres", "woon", "wonen", "woning", "verhuis", "verhuizen", "verhuisd", "huis"],
        global_regex_flags=re.MULTILINE,  # case-sensitive: capitals carry the signal here
    )


def postcode_recognizer() -> PatternRecognizer:
    return PatternRecognizer(
        supported_entity="NL_POSTCODE",
        supported_language=LANGUAGE,
        patterns=[
            Pattern("postcode", r"\b[1-9]\d{3} ?[A-Z]{2}\b", 0.7),
            Pattern("postcode_lowercase_joined", r"\b[1-9]\d{3}[a-z]{2}\b", 0.6),
            # "2024 in" has the same shape as "1234 ab": only with a context word.
            Pattern("postcode_lowercase_spaced", r"\b[1-9]\d{3} [a-z]{2}\b", 0.2),
        ],
        context=["postcode", "adres", "woon"],
        global_regex_flags=0,
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


def signature_recognizer() -> PatternRecognizer:
    """The line under a closing formula is almost always the writer's name, and NER
    often misses it because a lone name has no sentence around it."""
    closing = r"(?:groet(?:en|jes)?|Hoogachtend|gr\.?)"
    return PatternRecognizer(
        supported_entity="NL_SIGNATURE_NAME",
        supported_language=LANGUAGE,
        patterns=[
            Pattern(
                "name_after_closing",
                rf"(?<={closing},?[ ]*\n\s*)[A-Z]{_WORD_CHAR}+(?:[ ]{_WORD_CHAR}+){{0,5}}[ ]*$",
                0.6,
            )
        ],
        global_regex_flags=re.MULTILINE | re.IGNORECASE,
    )
