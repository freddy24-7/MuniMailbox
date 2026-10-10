import json
from dataclasses import asdict, dataclass

from app.privacy.crypto import Cipher


@dataclass(frozen=True)
class OriginalEmail:
    """The email as it arrived. Exists in memory only; stored encrypted as one value."""

    sender: str
    subject: str
    body: str

    def encrypt(self, cipher: Cipher) -> bytes:
        return cipher.encrypt(json.dumps(asdict(self), ensure_ascii=False))

    @classmethod
    def decrypt(cls, cipher: Cipher, token: bytes) -> "OriginalEmail":
        return cls(**json.loads(cipher.decrypt(token)))
