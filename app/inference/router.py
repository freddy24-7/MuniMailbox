import asyncio
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

import structlog
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import writer as audit
from app.db.models import PiiMapping, RedactionRecord
from app.inference.clients import ChatClient, Completion, EmbeddingClient
from app.inference.prompts import render_prompt
from app.observability import metrics
from app.privacy.crypto import Cipher
from app.privacy.redaction import RedactedText
from app.rag.chunking import PublicText

logger = structlog.get_logger(__name__)


class UnredactedInputError(Exception):
    """The router refused a model call. Never put the offending text in the message."""


class InvalidModelOutputError(Exception):
    """The model answered, but not in the shape that was asked for."""


@dataclass(frozen=True)
class InferenceResult[T]:
    output: T
    model: str
    left_local: bool


class InferenceRouter:
    """The only way to reach a model (CLAUDE.md rule 1, FR-13, FR-14).

    Callers hand over a prompt name and redacted variables, not a finished prompt, so the
    router can check exactly the text that came from the email.

    Routing: the local model answers unless the request is large or the local model
    fails; then the cloud model does, if one is configured. The same check guards both
    routes, so there is no path on which unredacted text is acceptable.

    The local server is one machine: chat and embedding calls to it share a limit on how
    many run at once, and the rest wait their turn. Cloud calls are not limited.
    """

    def __init__(
        self,
        *,
        local: ChatClient,
        cipher: Cipher,
        cloud: ChatClient | None = None,
        cloud_route_min_chars: int = 0,
        embedder: EmbeddingClient | None = None,
        local_max_concurrency: int = 1,
    ) -> None:
        self._local = local
        self._local_slots = asyncio.Semaphore(local_max_concurrency)
        self._cloud = cloud
        self._cloud_route_min_chars = cloud_route_min_chars
        self._embedder = embedder
        self._cipher = cipher

    async def complete(
        self,
        session: AsyncSession,
        *,
        email_id: uuid.UUID,
        actor: str,
        prompt_name: str,
        variables: Mapping[str, RedactedText],
        public_context: Mapping[str, PublicText] | None = None,
    ) -> InferenceResult[str]:
        """Free-text answer."""
        return await self._run(
            session, email_id, actor, prompt_name, variables, public_context, str, False
        )

    async def complete_json[T: BaseModel](
        self,
        session: AsyncSession,
        *,
        email_id: uuid.UUID,
        actor: str,
        prompt_name: str,
        variables: Mapping[str, RedactedText],
        schema: type[T],
        public_context: Mapping[str, PublicText] | None = None,
    ) -> InferenceResult[T]:
        """Answer validated against `schema`. An answer that does not fit counts as a
        failure of that model, like a timeout would."""

        def parse(text: str) -> T:
            try:
                return schema.model_validate_json(text)
            except ValidationError as error:
                # Count only: pydantic's message would quote the model's text.
                raise InvalidModelOutputError(
                    f"{error.error_count()} validation errors for {schema.__name__}"
                ) from None

        return await self._run(
            session, email_id, actor, prompt_name, variables, public_context, parse, True
        )

    async def _run[T](
        self,
        session: AsyncSession,
        email_id: uuid.UUID,
        actor: str,
        prompt_name: str,
        variables: Mapping[str, RedactedText],
        public_context: Mapping[str, PublicText] | None,
        parse: Callable[[str], T],
        json_mode: bool,
    ) -> InferenceResult[T]:
        # Only email-derived text is checked. Public documents legitimately contain words
        # that are personal data in an email (a city name, for instance).
        await self._require_redacted(session, email_id, variables.values())
        prompt = render_prompt(prompt_name, {**(public_context or {}), **variables})

        async def attempt(client: ChatClient) -> tuple[Completion, T]:
            completion = await client.complete(
                system=prompt.system, user=prompt.user, json_mode=json_mode
            )
            return completion, parse(completion.text)

        started = time.perf_counter()
        size = sum(len(text) for text in variables.values())
        left_local = self._cloud is not None and size > self._cloud_route_min_chars
        if not left_local:
            try:
                async with self._local_slots:
                    completion, output = await attempt(self._local)
            except Exception as error:
                if self._cloud is None:
                    raise
                logger.warning(
                    "inference.local_failed", prompt=prompt_name, error_type=type(error).__name__
                )
                metrics.LOCAL_FALLBACKS.inc()
                await audit.record(
                    session, actor=actor, action="inference.local_failed", email_id=email_id
                )
                left_local = True
        if left_local:
            assert self._cloud is not None  # noqa: S101  (narrowing; guaranteed above)
            completion, output = await attempt(self._cloud)

        await audit.record(
            session,
            actor=actor,
            action="inference.completed",
            email_id=email_id,
            model=completion.model,
            left_local=left_local,
        )
        route = "cloud" if left_local else "local"
        metrics.INFERENCE_CALLS.labels(route=route, model=completion.model).inc()
        metrics.INFERENCE_TOKENS.labels(route=route, kind="prompt").inc(completion.prompt_tokens)
        metrics.INFERENCE_TOKENS.labels(route=route, kind="completion").inc(
            completion.completion_tokens
        )
        logger.info(
            "inference.completed",
            prompt=prompt_name,
            model=completion.model,
            left_local=left_local,
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
            duration_ms=round((time.perf_counter() - started) * 1000),
        )
        return InferenceResult(output=output, model=completion.model, left_local=left_local)

    async def embed_query(
        self, session: AsyncSession, *, email_id: uuid.UUID, text: RedactedText
    ) -> list[float]:
        """Embedding of email text, for retrieval. Local only: there is no cloud route
        for embeddings. Guarded like any other model call."""
        await self._require_redacted(session, email_id, [text])
        return (await self._embed([text]))[0]

    async def embed_public(self, texts: list[str]) -> list[list[float]]:
        """Embeddings of public documents for the knowledge base. Takes no email and must
        never be given email text."""
        return await self._embed(texts)

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        if self._embedder is None:
            raise RuntimeError("no embedding model configured")
        async with self._local_slots:
            return await self._embedder.embed(texts)

    async def _require_redacted(
        self, session: AsyncSession, email_id: uuid.UUID, texts: Iterable[str]
    ) -> None:
        has_record = await session.scalar(
            select(RedactionRecord.id).where(RedactionRecord.email_id == email_id)
        )
        if has_record is None:
            metrics.INFERENCE_REFUSALS.labels(reason="no_redaction_record").inc()
            raise UnredactedInputError("no redaction record for this email")

        # Defence in depth: the type says "redacted", this checks it. If any value the
        # privacy agent replaced is present, the caller passed the wrong text.
        encrypted_values = await session.scalars(
            select(PiiMapping.encrypted_value).where(PiiMapping.email_id == email_id)
        )
        originals = [self._cipher.decrypt(value) for value in encrypted_values]
        for text in texts:
            if any(original in text for original in originals):
                metrics.INFERENCE_REFUSALS.labels(reason="unredacted_value").inc()
                raise UnredactedInputError("input contains a value that should be redacted")
