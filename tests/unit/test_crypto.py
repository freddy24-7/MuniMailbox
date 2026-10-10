import pytest
from cryptography.fernet import Fernet, InvalidToken

from app.privacy.crypto import Cipher


def test_round_trip_and_ciphertext_hides_the_value() -> None:
    cipher = Cipher(Fernet.generate_key().decode())
    token = cipher.encrypt("geheim gegeven")
    assert b"geheim" not in token
    assert cipher.decrypt(token) == "geheim gegeven"


def test_another_key_cannot_decrypt() -> None:
    token = Cipher(Fernet.generate_key().decode()).encrypt("geheim")
    with pytest.raises(InvalidToken):
        Cipher(Fernet.generate_key().decode()).decrypt(token)


def test_a_modified_token_is_rejected() -> None:
    cipher = Cipher(Fernet.generate_key().decode())
    token = bytearray(cipher.encrypt("geheim"))
    token[-5] ^= 1
    with pytest.raises(InvalidToken):
        cipher.decrypt(bytes(token))
