from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
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
    # Calls to the local model server (chat and embeddings) that may run at the same time.
    # 1 suits a laptop; raise it on a GPU server that batches requests (vLLM).
    local_llm_max_concurrency: int = Field(default=1, ge=1)

    # Cloud model, used only as a fallback and only with redacted text (FR-13, FR-14).
    # Off unless explicitly enabled.
    cloud_llm_enabled: bool = False
    cloud_llm_base_url: str = ""
    cloud_llm_model: str = ""
    cloud_llm_api_key: SecretStr | None = None
    cloud_llm_timeout_seconds: float = 60.0
    # Requests whose email text is longer than this skip the local model.
    cloud_route_min_chars: int = 6000

    # Knowledge base (RAG). Embeddings are always computed locally.
    embedding_model: str
    chroma_host: str = "localhost"
    chroma_port: int = 8001
    chroma_collection: str = "municipal_documents"
    chunk_max_chars: int = 1200
    retrieval_top_k: int = 4
    # Cosine similarity below which a chunk does not count as relevant. Depends on the
    # embedding model. Measured for bge-m3 with scripts/evaluate_retrieval.py: questions
    # with an answer score 0.53-0.74, questions without one 0.41-0.59. The ranges overlap,
    # so this is a trade-off: lower finds more answers and more wrong sources.
    retrieval_min_similarity: float = 0.55

    # A failed step is retried this many times before a human takes over (FR-3).
    max_step_retries: int = 2
    retry_backoff_seconds: float = 2.0
    # Which team handles which topic. Topics not listed stay with the team that has them.
    team_by_topic: dict[str, str] = {
        "permits": "Vergunningen en Wonen",
        "waste_collection": "Vergunningen en Wonen",
        "address_change": "Vergunningen en Wonen",
        "benefits": "Werk en Inkomen",
    }

    feed_path: Path = Path("data/synthetic/emails.jsonl")
    pipeline_workers: int = 5
    # Run independent agents at the same time. Off only for SQLite, which allows one writer.
    parallel_agents: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
