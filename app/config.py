from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration, read from the environment (and `.env` locally).

    A missing or malformed value stops the app at startup instead of at the first email.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: Literal["local", "test", "production"] = "local"
    # SecretStr keeps the value out of reprs, tracebacks and log lines.
    database_url: SecretStr
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"

    # Fernet keys. encryption_key protects stored personal data (GR-2); losing it makes
    # every stored original and mapping unreadable. session_secret protects login cookies.
    encryption_key: SecretStr
    session_secret: SecretStr
    session_ttl_seconds: int = 8 * 60 * 60

    spacy_model: str
    redaction_score_threshold: float = 0.4
    # The build fails if recall on the PII test set drops below this (GR-4).
    pii_recall_threshold: float = 0.95
    # Originals and PII mappings are deleted this long after an email arrived (GR-2).
    pii_retention_days: int = 30

    # Local model behind an OpenAI-compatible API (Ollama or vLLM).
    local_llm_base_url: str
    local_llm_model: str
    local_llm_timeout_seconds: float = 180.0

    feed_path: Path = Path("data/synthetic/emails.jsonl")
    pipeline_workers: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()
