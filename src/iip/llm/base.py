"""LLM provider interface (ADR-0005).

The rest of the code depends only on ``LLMProvider``, never on a vendor SDK, so a
self-hoster can switch between Gemini, Claude or a local Ollama model with a
config change, and tests use ``FakeProvider`` without network access.

Errors are split into two kinds because callers handle them differently:
- ``retryable=True``: rate limits (429), server errors (5xx), timeouts, dropped
  connections. Trying again later can succeed.
- ``retryable=False``: bad API key, malformed request, a response blocked by
  safety filters. Retrying gives the same answer, so don't burn quota on it.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int


class LLMError(Exception):
    def __init__(self, message: str, *, retryable: bool, status_code: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


class LLMProvider(Protocol):
    name: str
    model: str

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_output_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> LLMResponse: ...

    async def aclose(self) -> None: ...
