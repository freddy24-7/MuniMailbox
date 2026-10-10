import pytest
from pydantic import ValidationError

from app.config import Settings


def test_missing_database_url_fails_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_unknown_log_level_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "CHATTY")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_database_password_is_not_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:hunter2@localhost/db")
    settings = Settings(_env_file=None)
    assert "hunter2" not in repr(settings)
    assert "hunter2" in settings.database_url.get_secret_value()
