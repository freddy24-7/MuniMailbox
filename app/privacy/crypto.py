from cryptography.fernet import Fernet


class Cipher:
    """Symmetric, authenticated encryption for values stored at rest (GR-2).

    Fernet tokens are tamper-evident: decrypting a modified token raises `InvalidToken`.
    """

    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode())

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode())

    def decrypt(self, token: bytes) -> str:
        return self._fernet.decrypt(token).decode()
