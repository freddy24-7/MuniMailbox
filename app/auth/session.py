import uuid

from cryptography.fernet import Fernet, InvalidToken

SESSION_COOKIE = "session"


class SessionCodec:
    """Login cookie: the user's ID, encrypted and authenticated with a server-side key.
    A cookie that was altered, forged or is older than the TTL decodes to None."""

    def __init__(self, secret: str, ttl_seconds: int) -> None:
        self._fernet = Fernet(secret.encode())
        self._ttl_seconds = ttl_seconds

    def encode(self, user_id: uuid.UUID) -> str:
        return self._fernet.encrypt(user_id.bytes).decode()

    def decode(self, token: str) -> uuid.UUID | None:
        try:
            return uuid.UUID(bytes=self._fernet.decrypt(token.encode(), ttl=self._ttl_seconds))
        except (InvalidToken, ValueError):
            return None
