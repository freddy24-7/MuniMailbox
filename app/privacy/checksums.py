"""Checksum validation for Dutch identifiers, used by the PII recognizers (GR-1)
and to check that synthetic data is structurally valid."""

_BSN_WEIGHTS = (9, 8, 7, 6, 5, 4, 3, 2, -1)


def bsn_is_valid(candidate: str) -> bool:
    """The "11-check" (elfproef): the weighted digit sum must be divisible by 11."""
    if len(candidate) != 9 or not candidate.isdecimal() or candidate == "000000000":
        return False
    total = sum(int(digit) * weight for digit, weight in zip(candidate, _BSN_WEIGHTS, strict=True))
    return total % 11 == 0


def iban_is_valid(candidate: str) -> bool:
    """ISO 13616 mod-97 check. Spaces are allowed, as people write IBANs in groups of four."""
    iban = candidate.replace(" ", "").upper()
    if not 15 <= len(iban) <= 34 or not iban.isascii() or not iban.isalnum():
        return False
    if not iban[:2].isalpha() or not iban[2:4].isdecimal():
        return False
    rearranged = iban[4:] + iban[:4]
    return int("".join(str(int(char, 36)) for char in rearranged)) % 97 == 1
