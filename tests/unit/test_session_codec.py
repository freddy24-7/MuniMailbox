import uuid

from cryptography.fernet import Fernet

from app.auth.session import SessionCodec


def _codec(ttl_seconds: int = 60) -> SessionCodec:
    return SessionCodec(Fernet.generate_key().decode(), ttl_seconds)


def test_round_trip() -> None:
    codec, user_id = _codec(), uuid.uuid4()
    assert codec.decode(codec.encode(user_id)) == user_id


def test_forged_and_foreign_cookies_decode_to_nothing() -> None:
    codec = _codec()
    assert codec.decode("not-a-token") is None
    assert codec.decode(str(uuid.uuid4())) is None
    assert codec.decode(_codec().encode(uuid.uuid4())) is None  # signed with another key


def test_expired_cookie_decodes_to_nothing() -> None:
    codec = _codec(ttl_seconds=-1)
    assert codec.decode(codec.encode(uuid.uuid4())) is None
