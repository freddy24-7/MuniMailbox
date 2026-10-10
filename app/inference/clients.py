from dataclasses import dataclass
from typing import Protocol

import httpx


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int


class ChatClient(Protocol):
    async def complete(self, *, system: str, user: str) -> Completion: ...


class OpenAICompatibleClient:
    """Talks to anything that implements `/chat/completions`: Ollama, vLLM, cloud providers.

    Only `app.inference.router` may use a client. Agents go through the router.
    """

    def __init__(
        self, *, base_url: str, model: str, timeout_seconds: float, api_key: str | None = None
    ) -> None:
        self._model = model
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_seconds,
            headers={"Authorization": f"Bearer {api_key}"} if api_key else None,
        )

    async def complete(self, *, system: str, user: str) -> Completion:
        response = await self._http.post(
            "/chat/completions",
            json={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.2,
            },
        )
        response.raise_for_status()
        body = response.json()
        usage = body.get("usage") or {}
        return Completion(
            text=body["choices"][0]["message"]["content"].strip(),
            model=body.get("model", self._model),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        )

    async def aclose(self) -> None:
        await self._http.aclose()
