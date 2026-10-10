import pytest

from app.privacy.checksums import bsn_is_valid, iban_is_valid


def _bsn_with_check_digit(first_eight: str) -> str | None:
    """Derive the ninth digit instead of hardcoding a BSN that might belong to someone."""
    partial = sum(int(d) * w for d, w in zip(first_eight, (9, 8, 7, 6, 5, 4, 3, 2), strict=True))
    check = partial % 11
    return first_eight + str(check) if check < 10 else None


def test_bsn_accepts_a_number_that_passes_the_11_check() -> None:
    bsn = _bsn_with_check_digit("12345678")
    assert bsn is not None
    assert bsn_is_valid(bsn)


def test_bsn_rejects_a_changed_digit() -> None:
    bsn = _bsn_with_check_digit("12345678")
    assert bsn is not None
    changed = bsn[:-1] + str((int(bsn[-1]) + 1) % 10)
    assert not bsn_is_valid(changed)


@pytest.mark.parametrize("candidate", ["", "12345678", "1234567890", "12345678a", "000000000"])
def test_bsn_rejects_malformed_input(candidate: str) -> None:
    assert not bsn_is_valid(candidate)


# The example IBAN from the ISO 13616 / Dutch banking documentation, not a real account.
EXAMPLE_IBAN = "NL91ABNA0417164300"


def test_iban_accepts_valid_with_or_without_spaces() -> None:
    assert iban_is_valid(EXAMPLE_IBAN)
    assert iban_is_valid("NL91 ABNA 0417 1643 00")


@pytest.mark.parametrize(
    "candidate", ["NL92ABNA0417164300", "NL91ABNA041716430", "", "91NLABNA0417164300", "NL91-ABNA"]
)
def test_iban_rejects_invalid(candidate: str) -> None:
    assert not iban_is_valid(candidate)
